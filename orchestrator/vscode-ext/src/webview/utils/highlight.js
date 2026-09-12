/* Lightweight, dependency-free syntax highlighters.  Every function escapes
 * HTML first and returns an HTML string with <span class="syntax-*"> wrappers
 * (styles live in nodes.css / review.css).  Shared by the Collateral browser
 * and the run-review views. */

export function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

const VERILOG_KEYWORDS = new Set([
  'module', 'endmodule', 'input', 'output', 'inout', 'wire', 'reg',
  'logic', 'assign', 'always', 'always_ff', 'always_comb', 'begin',
  'end', 'if', 'else', 'case', 'endcase', 'casex', 'casez', 'default',
  'parameter', 'localparam', 'integer', 'genvar', 'generate',
  'endgenerate', 'for', 'while', 'function', 'endfunction', 'task',
  'endtask', 'return', 'posedge', 'negedge', 'or', 'and', 'not', 'xor',
  'initial', 'forever', 'repeat', 'do', 'fork', 'join', 'disable',
  'wait', 'macromodule', 'primitive', 'endprimitive', 'specify',
  'endspecify', 'typedef', 'enum', 'struct', 'packed', 'unique',
  'priority', 'unique0', 'package', 'endpackage', 'import', 'export',
  'class', 'endclass', 'extends', 'virtual', 'pure', 'static',
  'automatic', 'rand', 'randc', 'constraint', 'property', 'sequence',
  'signed', 'unsigned',
]);

/** Single-pass Verilog highlighter (comments, strings, sized numbers, keywords). */
export function highlightVerilog(text) {
  if (!text) return '';
  const out = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    const c2 = text[i + 1];
    if (c === '/' && c2 === '/') {
      let j = text.indexOf('\n', i);
      if (j < 0) j = n;
      out.push(`<span class="syntax-comment">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '/' && c2 === '*') {
      let j = text.indexOf('*/', i + 2);
      if (j < 0) j = n; else j += 2;
      out.push(`<span class="syntax-comment">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < n) {
        if (text[j] === '\\') { j += 2; continue; }
        if (text[j] === '"') { j++; break; }
        j++;
      }
      out.push(`<span class="syntax-string">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '`') {
      const m = text.slice(i).match(/^`[A-Za-z_][A-Za-z0-9_]*/);
      if (m) {
        out.push(`<span class="syntax-attr">${escHtml(m[0])}</span>`);
        i += m[0].length;
        continue;
      }
    }
    const sizedMatch = text.slice(i).match(/^\d*'(?:[bdohBDOH])[0-9a-fA-F_xzXZ?]+/);
    if (sizedMatch) {
      out.push(`<span class="syntax-number">${escHtml(sizedMatch[0])}</span>`);
      i += sizedMatch[0].length;
      continue;
    }
    if (/[0-9]/.test(c)) {
      const numMatch = text.slice(i).match(/^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/);
      if (numMatch) {
        out.push(`<span class="syntax-number">${escHtml(numMatch[0])}</span>`);
        i += numMatch[0].length;
        continue;
      }
    }
    if (/[a-zA-Z_$]/.test(c)) {
      const idMatch = text.slice(i).match(/^[a-zA-Z_$][a-zA-Z0-9_$]*/);
      if (idMatch) {
        const ident = idMatch[0];
        if (VERILOG_KEYWORDS.has(ident)) {
          out.push(`<span class="syntax-keyword">${ident}</span>`);
        } else if (ident.startsWith('$')) {
          out.push(`<span class="syntax-attr">${escHtml(ident)}</span>`);
        } else {
          out.push(escHtml(ident));
        }
        i += ident.length;
        continue;
      }
    }
    out.push(escHtml(c));
    i++;
  }
  return out.join('');
}

const TCL_KEYWORDS = new Set([
  'set', 'unset', 'if', 'else', 'elseif', 'for', 'foreach', 'while',
  'switch', 'proc', 'return', 'break', 'continue', 'expr', 'puts',
  'gets', 'string', 'lindex', 'llength', 'lappend', 'list', 'array',
  'dict', 'global', 'upvar', 'variable', 'namespace', 'source',
  'package', 'catch', 'error', 'eval', 'exec', 'open', 'close', 'read',
  'regexp', 'regsub', 'format', 'scan', 'incr', 'append', 'concat',
  'create_clock', 'create_generated_clock', 'set_input_delay',
  'set_output_delay', 'set_clock_uncertainty', 'set_clock_latency',
  'set_clock_groups', 'set_false_path', 'set_multicycle_path',
  'set_max_delay', 'set_min_delay', 'set_disable_timing',
  'set_load', 'set_drive', 'set_driving_cell', 'set_max_fanout',
  'set_max_transition', 'set_max_capacitance', 'set_min_capacitance',
  'set_case_analysis', 'set_propagated_clock', 'group_path',
  'all_inputs', 'all_outputs', 'all_clocks', 'all_registers',
  'get_ports', 'get_pins', 'get_nets', 'get_cells', 'get_clocks',
  'current_design', 'read_sdc', 'write_sdc', 'check_timing',
  'report_checks', 'report_wns', 'report_tns', 'read_liberty', 'link_design',
  'design', 'read_verilog', 'synth', 'flatten', 'opt', 'abc', 'write_verilog',
  'hierarchy', 'memory', 'opt_clean', 'stat', 'check', 'dfflibmap',
  'blackbox', 'chparam', 'memory_bram', 'memory_map',
]);

export function highlightTcl(text) {
  if (!text) return '';
  const out = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '#' && (i === 0 || /\s/.test(text[i - 1]))) {
      let j = text.indexOf('\n', i);
      if (j < 0) j = n;
      out.push(`<span class="syntax-comment">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < n && text[j] !== '"') {
        if (text[j] === '\\') j += 2; else j++;
      }
      j = Math.min(j + 1, n);
      out.push(`<span class="syntax-string">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '$' && /[A-Za-z_]/.test(text[i + 1] || '')) {
      const m = text.slice(i).match(/^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/);
      if (m) {
        out.push(`<span class="syntax-attr">${escHtml(m[0])}</span>`);
        i += m[0].length;
        continue;
      }
    }
    if (/[0-9]/.test(c)) {
      const m = text.slice(i).match(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/);
      if (m) {
        out.push(`<span class="syntax-number">${escHtml(m[0])}</span>`);
        i += m[0].length;
        continue;
      }
    }
    if (/[A-Za-z_]/.test(c)) {
      const m = text.slice(i).match(/^[A-Za-z_][A-Za-z0-9_]*/);
      if (m) {
        const ident = m[0];
        if (TCL_KEYWORDS.has(ident)) {
          out.push(`<span class="syntax-keyword">${ident}</span>`);
        } else {
          out.push(escHtml(ident));
        }
        i += ident.length;
        continue;
      }
    }
    out.push(escHtml(c));
    i++;
  }
  return out.join('');
}

const PY_KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break',
  'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally',
  'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal',
  'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
]);

export function highlightPython(text) {
  if (!text) return '';
  const out = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '#') {
      let j = text.indexOf('\n', i);
      if (j < 0) j = n;
      out.push(`<span class="syntax-comment">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    const triple = text.startsWith('"""', i) ? '"""' : text.startsWith("'''", i) ? "'''" : null;
    if (triple) {
      let j = text.indexOf(triple, i + 3);
      if (j < 0) j = n; else j += 3;
      out.push(`<span class="syntax-string">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && text[j] !== c && text[j] !== '\n') {
        if (text[j] === '\\') j += 2; else j++;
      }
      j = Math.min(j + 1, n);
      out.push(`<span class="syntax-string">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    if (c === '@' && /[A-Za-z_]/.test(text[i + 1] || '')) {
      const m = text.slice(i).match(/^@[A-Za-z_][A-Za-z0-9_.]*/);
      if (m) {
        out.push(`<span class="syntax-attr">${escHtml(m[0])}</span>`);
        i += m[0].length;
        continue;
      }
    }
    if (/[0-9]/.test(c) && !/[A-Za-z0-9_]/.test(text[i - 1] || '')) {
      const m = text.slice(i).match(/^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
      if (m) {
        out.push(`<span class="syntax-number">${escHtml(m[0])}</span>`);
        i += m[0].length;
        continue;
      }
    }
    if (/[A-Za-z_]/.test(c)) {
      const m = text.slice(i).match(/^[A-Za-z_][A-Za-z0-9_]*/);
      if (m) {
        const ident = m[0];
        if (PY_KEYWORDS.has(ident)) {
          out.push(`<span class="syntax-keyword">${ident}</span>`);
        } else if (text[i + ident.length] === '(' && /^(def|class)\s/.test(text.slice(Math.max(0, i - 6), i))) {
          out.push(`<span class="syntax-tag">${escHtml(ident)}</span>`);
        } else {
          out.push(escHtml(ident));
        }
        i += ident.length;
        continue;
      }
    }
    out.push(escHtml(c));
    i++;
  }
  return out.join('');
}

export function highlightMarkdown(text) {
  if (!text) return '';
  const lines = text.split('\n');
  const out = [];
  let inFence = false;
  let fenceLang = '';
  let fenceBuf = [];

  function flushFence() {
    const inner = escHtml(fenceBuf.join('\n'));
    const langTag = fenceLang ? ` <span class="syntax-attr">${escHtml(fenceLang)}</span>` : '';
    out.push(`<span class="syntax-md-code-block"><span class="syntax-md-fence">\`\`\`${langTag}</span>\n${inner}\n<span class="syntax-md-fence">\`\`\`</span></span>`);
    fenceBuf = [];
    fenceLang = '';
  }

  for (const line of lines) {
    if (inFence) {
      if (/^```\s*$/.test(line)) {
        flushFence();
        inFence = false;
      } else {
        fenceBuf.push(line);
      }
      continue;
    }
    const fenceStart = line.match(/^```(\w*)/);
    if (fenceStart) {
      inFence = true;
      fenceLang = fenceStart[1];
      continue;
    }
    if (/^(#{1,6})\s+(.*)$/.test(line)) {
      out.push(`<span class="syntax-md-heading">${escHtml(line)}</span>`);
      continue;
    }
    const list = line.match(/^(\s*)([-*+]|\d+\.)\s/);
    if (list) {
      const lead = line.slice(0, list[0].length);
      const rest = line.slice(list[0].length);
      out.push(`<span class="syntax-md-list">${escHtml(lead)}</span>${inlineMd(rest)}`);
      continue;
    }
    out.push(inlineMd(line));
  }
  if (inFence) flushFence();
  return out.join('\n');
}

function inlineMd(line) {
  const out = [];
  let i = 0;
  const n = line.length;
  while (i < n) {
    const c = line[i];
    if (c === '`') {
      const end = line.indexOf('`', i + 1);
      if (end > i) {
        out.push(`<span class="syntax-md-code">${escHtml(line.slice(i, end + 1))}</span>`);
        i = end + 1;
        continue;
      }
    }
    if (c === '*' && line[i + 1] === '*') {
      const end = line.indexOf('**', i + 2);
      if (end > i + 2) {
        out.push(`<span class="syntax-md-bold">**${escHtml(line.slice(i + 2, end))}**</span>`);
        i = end + 2;
        continue;
      }
    }
    if (c === '*') {
      const end = line.indexOf('*', i + 1);
      if (end > i + 1 && !/^\s/.test(line[i + 1])) {
        out.push(`<span class="syntax-md-em">${escHtml(line.slice(i, end + 1))}</span>`);
        i = end + 1;
        continue;
      }
    }
    if (c === '[') {
      const closeBr = line.indexOf('](', i + 1);
      const endParen = closeBr > i ? line.indexOf(')', closeBr + 2) : -1;
      if (closeBr > i && endParen > closeBr) {
        out.push(`<span class="syntax-md-link">${escHtml(line.slice(i, endParen + 1))}</span>`);
        i = endParen + 1;
        continue;
      }
    }
    out.push(escHtml(c));
    i++;
  }
  return out.join('');
}

export function highlightHtml(text) {
  if (!text) return '';
  let escaped = escHtml(text);
  escaped = escaped.replace(/(&lt;\/?)([a-zA-Z][a-zA-Z0-9-]*)/g,
    (m, lead, tag) => `${lead}<span class="syntax-tag">${tag}</span>`);
  escaped = escaped.replace(/([a-zA-Z-]+)=&quot;([^&]*?)&quot;/g,
    (m, attr, val) => `<span class="syntax-attr">${attr}</span>=<span class="syntax-string">"${val}"</span>`);
  escaped = escaped.replace(/&lt;!--([\s\S]*?)--&gt;/g,
    (m) => `<span class="syntax-comment">${m}</span>`);
  return escaped;
}

export function highlightJson(text) {
  if (!text) return '';
  const escaped = escHtml(text);
  return escaped.replace(
    /("(\\.|[^"\\])*")\s*:|("(\\.|[^"\\])*")|\b(true|false|null)\b|\b(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b/g,
    (match, key, _k2, str, _s2, kw, num) => {
      if (key) {
        const k = key.replace(/:$/, '').trim();
        return `<span class="json-key">${k}</span>:`;
      }
      if (str) return `<span class="json-string">${str}</span>`;
      if (kw) return `<span class="json-${kw}">${kw}</span>`;
      if (num) return `<span class="json-number">${num}</span>`;
      return match;
    },
  );
}

/** Tool-log highlighter: colours error/warning/pass/fail lines and headers. */
export function highlightLog(text) {
  if (!text) return '';
  return text.split('\n').map((line) => {
    const e = escHtml(line);
    if (/^=== .* ===\s*$/.test(line) || /^\d+(\.\d+)*\. /.test(line)) return `<span class="syntax-log-header">${e}</span>`;
    // cocotb summary lines mention FAIL=0 on a passing run: judge them first.
    if (/TESTS=\d+ PASS=\d+ FAIL=0\b/.test(line)) return `<span class="syntax-log-pass">${e}</span>`;
    if (/TESTS=\d+ PASS=\d+ FAIL=[1-9]/.test(line)) return `<span class="syntax-log-error">${e}</span>`;
    if (/%Error|\bERROR\b|Traceback|AssertionError|\bFAIL\b|\bFAILED\b/.test(line)) return `<span class="syntax-log-error">${e}</span>`;
    if (/%Warning|\bWARNING\b|\bWARN\b/.test(line)) return `<span class="syntax-log-warn">${e}</span>`;
    if (/\bPASS\b|\bPASSED\b/.test(line)) return `<span class="syntax-log-pass">${e}</span>`;
    if (/^(Command|Return code|Block|Attempt|Timestamp):/.test(line)) return `<span class="syntax-log-meta">${e}</span>`;
    return e;
  }).join('\n');
}

/** Pick a highlighter from a file extension (or explicit language). */
export function highlightFor(extOrLang, text) {
  switch ((extOrLang || '').toLowerCase()) {
    case '.v': case '.sv': case '.vh': case 'verilog':
      return highlightVerilog(text);
    case '.py': case 'python':
      return highlightPython(text);
    case '.json': case '.map': case 'json':
      return highlightJson(text);
    case '.sdc': case '.tcl': case '.ys': case 'tcl':
      return highlightTcl(text);
    case '.md': case 'markdown':
      return highlightMarkdown(text);
    case '.html': case '.htm': case 'html':
      return highlightHtml(text);
    case '.log': case '.txt': case '.rpt': case 'log':
      return highlightLog(text);
    default:
      return escHtml(text);
  }
}
