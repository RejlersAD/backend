"""RADAI Team & Enterprise tiers: teams, org governance policies, org admins.

Additive module. It does not change the behaviour of existing deliverables
(P&ID, PFD, CRS, line list, RADAI Intelligence). Team access to AI projects is
granted through the existing ``core.AIWorkspaceMember`` rows, so the AI
workspace's own access rules stay the single source of truth.
"""
