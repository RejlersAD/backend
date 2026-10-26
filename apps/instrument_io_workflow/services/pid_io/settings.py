"""Soft-coded thresholds for the P&ID IO extraction engine.

Every number here was measured on the reference drawing set (13-PP-152-45100 /
72010 / 72020 / 72030, Rev Q). They are project-settable defaults — a project
may override any of them in its settings JSON; code must read them from here,
never inline.
"""

# §6.1.1 — smart-tag layer
SMART_TAG_PREFIXES = ('IN#', 'EQ#')
SMART_TAG_MAX_SIZE_PT = 1.5          # hidden layer renders at 1.1 pt
SMART_TAG_WHITE = 0xFFFFFF           # colour int for white text

# §6.1.2 — bubble tag pairing
TAG_TOP_RE = r'^\d{2}[A-Z]{1,6}$'          # unit + function letters (13-LT)
TAG_BOTTOM_RE = r'^\d{3,5}[A-Z]{0,2}\d?$'  # loop number + suffix (7201C)
TAG_PAIR_MIN_GAP_PT = 5.0                  # top→bottom vertical gap range
TAG_PAIR_MAX_GAP_PT = 9.5
TAG_PAIR_MAX_CENTRE_DX_PT = 4.5            # horizontal centre alignment

# §6.1.3 — bubble outline
BUBBLE_MIN_SEGMENTS = 16
BUBBLE_MIN_SIZE_PT = 14.0
BUBBLE_MAX_SIZE_PT = 28.0
BUBBLE_SQUARE_EDGE_TOL_PT = 2.0            # bbox edge match for square frame
BUBBLE_BAR_TOL_PT = 2.0                    # centre bar vertical centre match

# §6.1.4 — system letter under the loop text
SYSTEM_LETTERS = ('I', 'S', 'P', 'PX', 'M', 'K', 'H')
SYSTEM_LETTER_MIN_DY_PT = -2.0
SYSTEM_LETTER_MAX_DY_PT = 5.0
SYSTEM_LETTER_MAX_DX_PT = 4.5

# §6.1.5 — alarm letters around the bubble
ALARM_LETTERS = ('SHH', 'HH', 'H', 'SH', 'L', 'SL', 'LL', 'SLL')
ALARM_ZONE = (-4.0, -8.0, 16.0, 4.0)       # left, up, right, down of bbox
ALARM_EXCLUDE_RED = True

# §6.2.1 — equipment
EQUIP_LABEL_RE = r'^\d{2}-[A-Z]{2}-\d{3,4}(\.[IVX]+)?[A-Z]?$'
EQUIP_LABEL_MIN_SIZE_PT = 7.5
EQUIP_BODY_MIN_WIDTH_PT = 0.9
EQUIP_LABEL_PROXIMITY_PT = 90.0
EQUIP_BODY_MAX_AREA_PT2 = 20000.0

# §6.2.2 — pipes
PIPE_WIDTH_PT = 0.48
PIPE_WIDTH_TOL_PT = 0.06
PIPE_MIN_SEGMENT_PT = 3.0
PIPE_PEN_DEFAULT = 'red'                   # project setting: new piping colour
PIPE_RUN_MAX_GAP_PT = 60.0                 # merge collinear pieces across valves

# §6.2.4 — line labels  (size"-unit-service-serial-class[-insulation])
LINE_LABEL_RE = (
    r'(\d+(?:\s?\d/\d)?(?:/\d+)?")-(\d{2})-([A-Z]{2})-(\d{4,6})'
    r'-([A-Z0-9]+)(?:-([A-Z]{1,3}))?'
)
LINE_LABEL_ON_LINE_PT = 4.0
LEADER_MAX_STROKES = 6
LEADER_MIN_PIPE_RUN_PT = 25.0

# §6.3 — instrument → process connection
SIGNAL_WIDTH_PT = 0.24
SIGNAL_MIN_LEN_PT = 2.2
CONTACT_PIPE_TOL_PT = 1.6
CONTACT_EQUIP_NOZZLE_TOL_PT = 18.0
LEADER_WIDTH_PT = 0.48
LEADER_MIN_LEN_PT = 4.0
LEADER_MAX_LEN_PT = 40.0
LEADER_TIP_EXTEND_PT = 22.0
LEADER_MIN_RUN_PT = 12.0
LINE_VIA_RUN_MAX_HOPS = 3

# §6.5 — service confidence bands
CONFIDENCE_HIGH = 0.85
CONFIDENCE_REVIEW = 0.6
CONNECTOR_RE = r'\d{2}-P[PU]-152-\d{5,6}-P2'
UTILITY_FLUIDS_RE = r'FLARE|NITROGEN|VENT|DRAIN|STEAM'
UTILITY_EXEMPT_SERVICE_CODES = ('WG', 'WL', 'FL', 'NI')

# §6.6 — set points
SETPOINT_VALUE_RE = r'[+-]?\d{2,5}'
SETPOINT_VALUE_MAX_DX_PT = 20.0
SETPOINT_SCALE_MAX_DIST_PT = 80.0
PSV_SET_RE = r'\d+(?:\.\d+)?\s*barg'
PSV_SET_MAX_DY_PT = 26.0
RED_COLOUR = 0xFF0000

# §6.7 — IO class
NO_IO_FUNCTIONS = ('PG', 'TG', 'LG', 'FG', 'PSV', 'TSV', 'PCV', 'PSE', 'RO',
                   'FO', 'SP', 'TW', 'FE', 'XV', 'FV', 'LV', 'PV', 'TV', 'HV')
PACKAGE_SYSTEMS = ('P', 'PX')
