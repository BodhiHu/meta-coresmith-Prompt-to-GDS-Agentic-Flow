import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { PassFail, BudgetCell, Collapsible, Table, LongText, KV, StatusBadge } from '../common';

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

describe('review primitives', () => {
  test('PassFail is tri-state', () => {
    const { container, rerender } = render(<PassFail value={true} />);
    expect(container.textContent).toContain('pass');
    expect(container.querySelector('.rv-badge-ok')).not.toBeNull();
    rerender(<PassFail value={false} no="broken" />);
    expect(container.textContent).toContain('broken');
    expect(container.querySelector('.rv-badge-fail')).not.toBeNull();
    rerender(<PassFail value={null} none="not run" />);
    expect(container.textContent).toContain('not run');
    expect(container.querySelector('.rv-badge-muted')).not.toBeNull();
  });

  test('StatusBadge maps status to tone', () => {
    const { container } = render(<StatusBadge status="incomplete" />);
    expect(container.querySelector('.rv-badge-warn')).not.toBeNull();
  });

  test('BudgetCell colours over-budget values', () => {
    const { container, rerender } = render(<BudgetCell measured={6446} budget={4400} />);
    expect(container.querySelector('.rv-budget-fail')).not.toBeNull();
    expect(container.textContent).toContain('6,446');
    expect(container.textContent).toContain('147%');
    rerender(<BudgetCell measured={100} budget={4400} />);
    expect(container.querySelector('.rv-budget-ok')).not.toBeNull();
    rerender(<BudgetCell measured={null} />);
    expect(container.textContent).toBe('--');
  });

  test('Collapsible toggles by click and keyboard, right slot does not toggle', () => {
    const onRight = jest.fn();
    render(
      <Collapsible title="Sys" subtitle="12 chars" right={<button type="button" onClick={onRight}>chip</button>}>
        <span>BODY</span>
      </Collapsible>,
    );
    expect(screen.queryByText('BODY')).toBeNull();
    fireEvent.click(screen.getByText('Sys'));
    expect(screen.getByText('BODY')).toBeInTheDocument();
    fireEvent.click(screen.getByText('chip'));
    expect(onRight).toHaveBeenCalled();
    expect(screen.getByText('BODY')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: /Sys/ }), { key: 'Enter' });
    expect(screen.queryByText('BODY')).toBeNull();
  });

  test('Table renders columns, custom renderers and row clicks', () => {
    const onRow = jest.fn();
    render(
      <Table
        columns={[{ key: 'name', label: 'Block' }, { key: 'wns', label: 'WNS', render: (r) => `${r.wns} ns` }]}
        rows={[{ name: 'a', wns: 1.5 }, { name: 'b', wns: -0.2 }]}
        rowKey={(r) => r.name}
        onRowClick={onRow}
      />,
    );
    expect(screen.getByText('-0.2 ns')).toBeInTheDocument();
    fireEvent.click(screen.getByText('b'));
    expect(onRow).toHaveBeenCalledWith({ name: 'b', wns: -0.2 });
    const { container } = render(<Table columns={[{ key: 'x', label: 'X' }]} rows={[]} empty="nothing" />);
    expect(container.textContent).toContain('nothing');
  });

  test('KV drops empty values and renders booleans as badges', () => {
    const { container } = render(<KV rows={[['A', 'x'], ['B', null], ['C', true], ['D', [1, 2]]]} />);
    expect(container.textContent).toContain('A');
    expect(container.textContent).not.toContain('B');
    expect(container.querySelector('.rv-badge-ok')).not.toBeNull();
    expect(container.textContent).toContain('1, 2');
  });

  test('LongText searches, counts matches and expands', () => {
    const text = `${'a'.repeat(50)} needle ${'b'.repeat(50)} needle`;
    const { container } = render(<LongText text={text} initialChars={20} />);
    // Collapsed by default with a show-all control.
    expect(screen.getByText(/Show all/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'NEEDLE' } });
    expect(container.querySelectorAll('mark.rv-mark').length).toBe(2);
    expect(screen.getByText('1/2')).toBeInTheDocument();
    fireEvent.click(screen.getByText('▼'));
    expect(screen.getByText('2/2')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: '' } });
    fireEvent.click(screen.getByText(/Show all/));
    expect(container.querySelector('.rv-longtext-body').textContent.length).toBe(text.length);
  });

  test('LongText never injects markup from the text', () => {
    const { container } = render(<LongText text={'<img src=x onerror=alert(1)>'} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('.rv-longtext-body').textContent).toContain('<img');
  });
});
