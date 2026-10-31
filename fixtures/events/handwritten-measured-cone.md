# Hand-written measured caller fixture

`handwritten-measured-cone.jsonl` is illustrative UI evidence, not a real recording or a test
result. It reuses the hand-written level 1 run through seq 20. The four subsequent facts events
show comparisons available (21), partial measurements (22), unavailable recording with its
published reason (23), and measurements not recorded (24). Counts describe invocations.
No provider API or test execution produced these values. The browser tests load each event
through the normal state reducer and label every capture as a hand-written fixture.
