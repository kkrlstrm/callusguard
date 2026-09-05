"""callusguard — record what your agent did, learn from it, enforce, verify.

A closed loop for agentic coding:

    telemetry   record execution traces
    derive      mine recurring failures into candidate rules
    learn       persist cross-session knowledge and distill reviewable interventions
    guard       enforce at the tool boundary
    wroteonly   verify the run stayed in scope
    lifecycle   prune rules that stopped earning their place

The learning plane is offline and never auto-activates an intervention. The
synchronous enforcement path remains dependency-free, network-free, and model-free.
"""

__version__ = "0.6.0"
