# Minimal stub for microgpt canary tests. Output format must match parser (step / loss, sample N: name).
num_steps = 1000
for step in range(min(1, num_steps)):
    print(f"step {step+1:4d} / {num_steps:4d} | loss 1.2345")
print("\n--- inference (new, hallucinated names) ---")
for sample_idx in range(20):
    print(f"sample {sample_idx+1:2d}: StubName{sample_idx+1}")
