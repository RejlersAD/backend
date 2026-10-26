"""Deterministic P&ID → Instrument I/O List extraction engine.

One module per spec step (see the IO List alignment spec §6):
  spans.py       §6.1.1  span/line/word reading + hidden smart-tag layer
  tags.py        §6.1    bubble tag pairing, outline class, system letter, alarms
  pipes.py       §6.2    equipment, pipe runs, line labels + anchoring
  connections.py §6.3–4  instrument→process contact, line number per tag
  service.py     §6.5    service text generation + confidence + evidence
  setpoints.py   §6.6    set points from vessel scales / PSV text
  ioclass.py     §6.7    IO class rules + derived valve-typical rows
  vendor.py      §6.8    vendor IO database auto-fill + conflicts
  baseline.py    §3      IO list baseline workbook reading
  engine.py              orchestrates all steps into one extraction result

Everything is deterministic and unit-testable against the reference files;
all tolerances/thresholds are soft-coded in settings.py.
"""
