# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Sky130 DRC template (Magic VLSI)
# Runs full design-rule check on the flattened layout.
#
# Variables to substitute: TECH_LEF, CELL_LEF, CELL_GDS, DEF_FILE,
#                          BLOCK_NAME, OUT_DIR

lef read $TECH_LEF
lef read $CELL_LEF
gds read $CELL_GDS
def read $DEF_FILE
load $BLOCK_NAME

# Flatten for DRC
flatten ${BLOCK_NAME}_flat
load ${BLOCK_NAME}_flat
select top cell
drc catchup
drc count
set drc_result [drc listall why]
set drc_count [drc listall count total]

set drc_rpt [open "$OUT_DIR/magic_drc.rpt" w]
puts $drc_rpt "Design: $BLOCK_NAME"
puts $drc_rpt "DRC count: $drc_count"
puts $drc_rpt $drc_result
close $drc_rpt
if {![string is integer -strict $drc_count] || $drc_count < 0} {
    puts stderr "ERROR: Magic returned a non-numeric DRC count: <$drc_count>"
    exit 1
}

puts "DRC violations: $drc_count"
puts "DRC report: $OUT_DIR/magic_drc.rpt"
quit -noprompt
