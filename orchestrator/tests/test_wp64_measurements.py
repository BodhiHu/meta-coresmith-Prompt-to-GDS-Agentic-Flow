"""Evidence scope must survive verdict and report rendering."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from orchestrator.harness import gate_sim as gs
from orchestrator.langgraph import final_report as fr
from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.tests.candidate_fixtures import adopt
from orchestrator.tests.test_gate_sim import _BLOCK, _good_ref, _runner_for, _stub_env


def test_maximum_marker_is_scope_not_executed_evidence(tmp_path, monkeypatch):
    (tmp_path/'inputs').mkdir()
    (tmp_path/'inputs/task.yaml').write_text('max_geometry_cases:\n  depth_limit:\n    queue_depth: 64\n')
    tb = tmp_path / 'tb.py'
    tb.write_text('# MAXGEO: unrelated=64\n')
    monkeypatch.setattr(pg, '_maxgeo_gate_enabled', lambda: True)
    monkeypatch.setattr('orchestrator.langgraph.bfm_lib.maxgeo.declared_dimensional_maxima', lambda _: {'queue_depth':64})
    result = pg._maxgeo_gate_verdict(str(tmp_path),str(tb))
    assert result['verdict'] == 'unknown'
    assert result['reason']
    result = pg._maxgeo_gate_verdict(str(tmp_path),str(tb),{'deterministic_bfm':True,'contract':{'bus':'qspi'}})
    assert result['verdict'] == 'unknown'


def test_maximum_requires_executed_owner_declared_case(tmp_path, monkeypatch):
    (tmp_path/'inputs').mkdir()
    (tmp_path/'inputs/task.yaml').write_text('max_geometry_cases:\n  depth_limit:\n    queue_depth: 64\n')
    tb = tmp_path/'tb.py'
    tb.write_text('# MAXGEO: queue_depth=64\n')
    monkeypatch.setattr(pg, '_maxgeo_gate_enabled', lambda: True)
    monkeypatch.setattr('orchestrator.langgraph.bfm_lib.maxgeo.declared_dimensional_maxima', lambda _: {'queue_depth':64})
    for cases, passed, expected in [([],True,'unknown'),(['depth_limit'],False,'unknown'),(['depth_limit'],True,'pass'),(['different'],True,'unknown')]:
        result = pg._maxgeo_gate_verdict(str(tmp_path),str(tb),sim_result={'passed':passed,'executed_cases':cases})
        assert result['verdict'] == expected


@pytest.mark.parametrize('limit,cycles,bits,expected', [(100,1,1,'bounded'),(3,3,27,'bounded'),(100,6,54,'pass')])
def test_gate_sim_full_and_partial_comparison_counts(tmp_path,monkeypatch,limit,cycles,bits,expected):
    net,pdk = _stub_env(monkeypatch,tmp_path)
    monkeypatch.setattr(gs,'gate_sim_max_cycles',lambda:limit)
    monkeypatch.setattr(gs,'build_and_run_gate_sim',lambda **_: {'ok':True,'cycles_compared':cycles,'output_bits_compared':bits,'diverged':False})
    result = gs.check_gate_sim(_BLOCK,str(net),'r.v','tb.py',sim_runner=_runner_for(_good_ref(tmp_path)),pdk_root=pdk,work_root=tmp_path/'work')
    assert result.status == expected
    assert result.ok is (expected=='pass')
    assert result.detail['reference_cycles'] == 6
    assert result.detail['recorded_cycles'] == min(6,limit)
    assert result.detail['compared_cycles'] == cycles


class Score:
    def __init__(self, top=False): self.top=top
    def latest_dv(self,*a,**k): return None
    def coverage_latest(self,*a,**k): return None
    def latest_ppa(self,name):
        if name=='leaf':
            return {'wns_ns':0,'cells':10}
        return {'wns_ns':-5,'cells':20} if self.top else None


@pytest.mark.parametrize('measured', [False,True])
def test_top_timing_never_uses_leaf_minimum(tmp_path,measured):
    top = tmp_path / 'chip_top.v'
    top.write_text('module chip_top(); endmodule\n')
    adopt(tmp_path, top)
    result = fr.build_final_report({'block_queue':[{'name':'leaf'}],'completed_blocks':[{'name':'leaf','success':True}],'design_name':'chip_top','target_clock_mhz':50},str(tmp_path),scoreboard=Score(measured))
    assert result['signoff']['top_fmax_mhz'] == (40 if measured else None)
    assert result['signoff']['leaf_estimate_fmax_mhz'] == 50
    text = fr.render_markdown(result)
    assert 'Leaf estimate' in text
    if not measured:
        assert 'Top Fmax: unknown' in text


def test_infrastructure_cap_counts_only_the_consecutive_streak(monkeypatch):
    monkeypatch.setenv('CORESMITH_INFRA_MAX_RETRIES','6')
    alternating = [{'category':c} for c in ['INFRASTRUCTURE_ERROR','FUNCTIONAL_ERROR']*6+['INFRASTRUCTURE_ERROR']]
    assert pg._route_decision({'category':'INFRASTRUCTURE_ERROR'},alternating,1,3,'rtl') == 'retry_rtl'
    assert pg._route_decision({'category':'INFRASTRUCTURE_ERROR'},[{'category':'INFRASTRUCTURE_ERROR'}]*6,1,3,'rtl') == 'ask_human'


def test_infrastructure_backoff_resets_after_functional_outcome(tmp_path,monkeypatch):
    class DB:
        def diagnosis(self,*a): return {'category':'INFRASTRUCTURE_ERROR'}
        def attempt_history(self,*a): return [{'diagnosis':{'category':c}} for c in ['INFRASTRUCTURE_ERROR']*5+['FUNCTIONAL_ERROR','INFRASTRUCTURE_ERROR']]
    monkeypatch.setattr(pg,'_db',lambda _:DB())
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio,'sleep',sleep)
    result = asyncio.run(pg.decide_node({'project_root':str(tmp_path),'current_block':{'name':'leaf'},'attempt':1,'max_attempts':3,'debug_action':'retry_rtl'}))
    sleep.assert_awaited_once_with(60)
    assert 'attempt' not in result


def test_infrastructure_fast_path_still_honors_cap(tmp_path,monkeypatch):
    class DB:
        def diagnosis(self,*a): return {'category':'INFRASTRUCTURE_ERROR'}
        def attempt_history(self,*a): return [{'category':'INFRASTRUCTURE_ERROR'}]*6
    monkeypatch.setenv('CORESMITH_INFRA_MAX_RETRIES','6')
    monkeypatch.setattr(pg,'_db',lambda _:DB())
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio,'sleep',sleep)
    result = asyncio.run(pg.decide_node({'project_root':str(tmp_path),'current_block':{'name':'leaf'},'attempt':1,'max_attempts':3,'debug_action':'retry_rtl'}))
    assert result['debug_action'] == 'ask_human'
    sleep.assert_not_awaited()


def test_only_successful_xml_rows_are_executed_case_evidence(tmp_path):
    from orchestrator.langgraph.integration_helpers import _successful_sim_cases
    path = tmp_path / 'results.xml'
    assert _successful_sim_cases(path) == []
    path.write_text('<testsuite><testcase name="owner_max"/>'
                    '<testcase name="skipped"><skipped/></testcase>'
                    '<testcase name="failed"><failure/></testcase>'
                    '<testcase name="error"><error/></testcase>'
                    '<testcase name="duplicate"/><testcase name="duplicate"><failure/></testcase>'
                    '</testsuite>')
    assert _successful_sim_cases(path) == ['owner_max']
    path.write_text('broken XML')
    assert _successful_sim_cases(path) == []


def test_bounded_gate_sim_blocks_consumers():
    result = gs.GateSimResult(ran=True, ok=False, status=gs.STATUS_BOUNDED)
    assert result.blocking
    assert result.as_dict()['status'] == 'bounded'
