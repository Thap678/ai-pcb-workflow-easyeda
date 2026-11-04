"""Snapshot the board's PCB (components, pads, tracks, vias, pours) to a JSON file.

usage: python dump.py <out.json>          (input for conn.py, gmetrics.py, stitch.py, render.py, place_from_dump.py)
Save the PCB first; after pour-rebuild / many edits run `easyeda --project <P> doc reload <PCB>` so the snapshot
reads fresh engine state.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as K

d = K.dump(sys.argv[1])
cu = d.get('copper', {})
print(sys.argv[1], '| components', len(d.get('components', [])), '| tracks', len(cu.get('lines', [])),
      '| vias', len(cu.get('vias', [])), '| pours', len(cu.get('pours', [])))
