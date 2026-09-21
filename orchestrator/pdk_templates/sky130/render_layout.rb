# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# KLayout batch renderer. Runtime variables are supplied with:
#   -rd input=<DEF-or-GDS> -rd output=<PNG> -rd width=<px> -rd height=<px>

raise "missing input layout" unless defined?($input) && File.file?($input)
raise "missing output path" unless defined?($output) && !$output.empty?

app = RBA::Application.instance
window = app.main_window
window.load_layout($input, 0)
view = window.current_view
raise "KLayout did not create a layout view for #{$input}" if view.nil?

view.max_hier
view.zoom_fit
view.save_image($output, $width.to_i, $height.to_i)
