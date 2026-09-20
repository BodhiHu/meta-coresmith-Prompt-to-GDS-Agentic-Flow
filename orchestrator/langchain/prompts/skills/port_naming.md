# Skill: canonical port naming (`<channel>_<field>`)

The interface contract is the SOLE naming authority for a block's ports.

## The rule

For every edge that touches this block, the contract names a channel (the
block's `producer_port` / `consumer_port`) and lists the signals on it
(`fields[]` + `sideband_signals[]` -- their UNION is the port set). The
canonical flattened RTL port name is:

    <channel>_<field>

A signal the contract lists with no channel (a bare sideband such as `clk`,
`rst_n`, or an externally mandated pad name) keeps its bare name.

## The handshake pair is part of the port set

`fields[]` describes the PAYLOAD. The channel's flow control lives in the
edge's `handshake_protocol` and in the `producer_port` / `consumer_port`
strings, which spell the channel with its handshake and data names, e.g.
`m_residual_srdy/m_residual_data` -> channel `m_residual`. Every channel
therefore exposes, IN ADDITION to `<channel>_<field>` for each payload field:

    handshake_protocol: srdy_drdy   ->  <channel>_srdy  and  <channel>_drdy
    handshake_protocol: axi_stream  ->  <channel>_tvalid and <channel>_tready
                                       (payload as <channel>_tdata unless the
                                        contract flattens it)

Direction follows the role: the producer drives `srdy`/`tvalid` and receives
`drdy`/`tready`; the consumer the reverse. A beat transfers on `srdy && drdy`
(or `tvalid && tready`) at the edge, and the payload fields must hold stable
while `srdy` is high and `drdy` is low. Example, consumer side of the edge
`intra16_chroma_mode_engine__m_residual__to__forward_transform_engine__s_residual`
(srdy_drdy, fields residual_samples[144], block_class[3], ...):

    input  wire         s_residual_srdy,
    output wire         s_residual_drdy,
    input  wire [143:0] s_residual_residual_samples,
    input  wire [2:0]   s_residual_block_class,
    ...

A channel emitted WITHOUT its handshake pair is unwireable: the assembler
cannot connect flow control, the per-block gate rejects the RTL before
simulation, and the chip lead sends the block back at integration review.
The `srdy_drdy` skill's rule against handshaking a compile-time-enumerable
sequence is about NOT inventing per-element request/response loops inside a
block; it never removes the contract's edge-level handshake.

## NEVER shorten a doubled token

When the channel suffix and the field prefix share a token, the token appears
TWICE. That is correct and required:

    channel: data_write        field: write_enable
    canonical port name:  data_write_write_enable      <-- CORRECT
    WRONG:                data_write_enable            <-- collapsed token
    WRONG:                write_enable                 <-- dropped channel

The doubled token is not a typo and not redundant. The deterministic
integration assembler resolves contract edges BY NAME: `data_write_enable`
does not resolve, the edge is unwireable, and the block is rejected by the
pre-simulation conformance gate. Two channels on one block routinely carry
the same field name (`read_enable`, `req_addr`, `valid`), and the channel
prefix is the only thing that tells them apart.

Same rule for a dropped or substituted prefix: `host_read_enable` is not an
acceptable spelling of `framebuffer_read_read_enable`.

## Precedence

1. **The contract's port table wins.** It is the frozen, authoritative naming
   source.
2. A golden / reference model's port identifiers are NOT authoritative. Models
   are written for simulation and routinely collapse or abbreviate names.
   Transcribe the model's BEHAVIOR byte-exact; take every port NAME from the
   contract table.
3. Accumulated per-block constraints, prior attempts' RTL, testbench code, and
   the uArch spec's prose are all subordinate to the contract table on naming.
   If any of them names a port differently, the contract wins -- silently and
   without asking.

## Checklist before emitting a module header

- [ ] Every contract signal on every edge touching this block has a port.
- [ ] Every channel has its handshake pair (`<channel>_srdy`/`_drdy` or
      `<channel>_tvalid`/`_tready`) with the direction of its role.
- [ ] Each port is spelled `<channel>_<field>` exactly, character for
      character, including any doubled token.
- [ ] No port wears a channel prefix that the contract does not declare.
- [ ] No signal is exposed twice (both `<channel>_<field>` and bare `<field>`).
