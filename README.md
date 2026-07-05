# VolMax BESS Reactive Capability Audit (SOH-Aware Derating)

An independent Proof of Concept (PoC) demonstrating how Battery State of Health (SOH) physically restricts the reactive power (MVAr) capability of Grid-Scale BESS, and how static grid compliance models produce false positives.

## The Industry Blind Spot
Most grid integration and load-flow studies treat Battery Energy Storage Systems (BESS) as static assets. If the inverter datasheet claims $\pm X$ MVAr, the grid compliance engine assumes the system can deliver $\pm X$ MVAr for its entire 15-year lifespan. 

**This is a physical fallacy.** 
As lithium-ion cells age, their internal resistance ($R_{int}$) increases and the Open Circuit Voltage (OCV) curve shifts. When providing maximum reactive power, the voltage sag/swell under high apparent current hits the inverter's DC-link voltage limits much earlier. The true reactive capability of a degraded battery is strictly bounded by its SOH.

## The VolMax Approach
This repository contains the VolMax `soh_to_capability` mapping function. It bridges the gap between electrochemical degradation and grid-side load-flow engines.

The function takes the battery state:
- **SOH (State of Health)**
- **R_int (Internal Resistance)**
- **OCV (Open Circuit Voltage)**

And dynamically calculates the **derated Q headroom** (`q_avail_mvar`). 

### The "Verdict Flip"
By running this model, we expose the discrepancy between *paper capability* and *physical capability*. A BESS that passes a Point of Common Coupling (PCC) voltage-droop compliance check at 95% SOH (inflated by leakage-optimistic models) can **fail** the exact same check at 80% SOH (honest aged state) because it no longer has the physical voltage headroom to push the required reactive current. This is demonstrated in the included `reproduce.py` example, where the compliance verdict flips at 2.8 MW active export under a 1.5 Mvar reactive support requirement.

## Integration (Pandapower)
This function is designed to be engine-agnostic but drops perfectly into open-source load-flow engines like `pandapower`.

Instead of using a static generator capability, map the VolMax output dynamically before running your power flow:
```python
# Standard static assumption (Flawed)
net.sgen.max_q_mvar = nameplate_q_mvar

# VolMax SOH-aware dynamic mapping (SOH-aware model)
q_avail_mvar = calculate_derated_q(soh, r_int, v_dc_link, nameplate_q_mvar)
net.sgen.max_q_mvar = q_avail_mvar
```

## Limitations
This Proof of Concept is a simplified physical model with the following boundaries:
1. **R_int to SOH Mapping**: The internal resistance scaling is a model assumption. Real-world cells exhibit significant physical variation (e.g., up to 1.9× resistance scatter at end-of-life), meaning SOH does not map deterministically to a single resistance value.
2. **Simplified DC-Link Dynamics**: The inverter DC-link voltage drop is simplified. Real utility-scale inverters employ complex internal control-loop strategies and active voltage management.
3. **Low-Frequency Resistance**: The model assumes a static low-frequency resistance ($R_{int}$), neglecting dynamic impedance responses under high-frequency grid transients.

## License

This code is released under the **Apache 2.0 License**. It is open for integration into broader power system analysis tools, provided the VolMax verification logic remains attributed.

---

*Developed by Ivan Nestorov | [VolMax Studio Lab](https://volmax-studio.rs) - Independent AI & Operations Verification for Battery Systems.*

*Concept emerged from a public exchange on Adam Kierad's Q-V droop post; all code, physics, and implementation are the author's own.*
