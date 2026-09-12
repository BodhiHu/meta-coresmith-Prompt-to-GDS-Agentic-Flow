import {
  escHtml, highlightVerilog, highlightPython, highlightLog, highlightJson, highlightTcl,
  highlightMarkdown, highlightFor,
} from '../highlight';

describe('highlighters', () => {
  test('escHtml neutralises markup', () => {
    expect(escHtml('<script>alert(1)</script> & "x"')).toBe('&lt;script&gt;alert(1)&lt;/script&gt; &amp; "x"');
  });

  test('verilog: keywords, comments, strings, sized numbers, macros', () => {
    const html = highlightVerilog("module m; // c\n  reg [7:0] q = 8'hFF; `define X\n  $display(\"hi <b>\"); endmodule");
    expect(html).toContain('<span class="syntax-keyword">module</span>');
    expect(html).toContain('<span class="syntax-comment">// c</span>');
    expect(html).toContain("<span class=\"syntax-number\">8'hFF</span>");
    expect(html).toContain('<span class="syntax-attr">`define</span>');
    expect(html).toContain('<span class="syntax-string">"hi &lt;b&gt;"</span>');
    expect(html).not.toContain('<b>');
  });

  test('python: keywords, decorators, strings, comments', () => {
    const html = highlightPython('@cocotb.test()\nasync def t(dut):\n    """doc"""\n    x = 0x1F  # cm\n    return "s"');
    expect(html).toContain('<span class="syntax-attr">@cocotb.test</span>');
    expect(html).toContain('<span class="syntax-keyword">async</span>');
    expect(html).toContain('<span class="syntax-string">"""doc"""</span>');
    expect(html).toContain('<span class="syntax-number">0x1F</span>');
    expect(html).toContain('<span class="syntax-comment"># cm</span>');
  });

  test('log: classifies error / warning / pass / header lines', () => {
    const html = highlightLog('=== LINT LOG ===\n%Warning-WIDTH: x\n%Error: boom\n** TESTS=2 PASS=2 FAIL=0 SKIP=0 **\nplain <x>');
    const lines = html.split('\n');
    expect(lines[0]).toContain('syntax-log-header');
    expect(lines[1]).toContain('syntax-log-warn');
    expect(lines[2]).toContain('syntax-log-error');
    expect(lines[3]).toContain('syntax-log-pass');
    expect(lines[4]).toBe('plain &lt;x&gt;');
  });

  test('json / tcl / markdown produce spans and stay escaped', () => {
    expect(highlightJson('{"a": 1, "b": true}')).toContain('<span class="json-key">"a"</span>');
    expect(highlightTcl('create_clock -period 20.0 [get_ports clk] # c')).toContain('<span class="syntax-keyword">create_clock</span>');
    const md = highlightMarkdown('# Title\n- item `code`\n```json\n{"x":1}\n```');
    expect(md).toContain('syntax-md-heading');
    expect(md).toContain('syntax-md-code');
    expect(md).toContain('syntax-md-code-block');
  });

  test('highlightFor dispatches on extension or language', () => {
    expect(highlightFor('.v', 'module')).toContain('syntax-keyword');
    expect(highlightFor('python', 'def')).toContain('syntax-keyword');
    expect(highlightFor('.log', '%Error: x')).toContain('syntax-log-error');
    expect(highlightFor('.bin', '<x>')).toBe('&lt;x&gt;');
  });
});
