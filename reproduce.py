#!/usr/bin/env python3
"""
reproduce.py — Reproducible verification script for BESS SOH-aware reactive capability.
"""

import sys
import json
from soh_capability import InverterSpec, BatteryState, compare_honest_vs_inflated

def run_verification():
    print("======================================================================")
    print("RUNNING SOH-AWARE REACTIVE CAPABILITY MODEL VERIFICATION")
    print("======================================================================")
    
    # 1. Define physical specs and battery states
    # Representative 3.45 MVA BESS inverter specs (550V AC, nominal 1100V DC link)
    inv = InverterSpec(
        s_rated_mva=3.45,
        v_dc_nominal_v=1100.0,
        i_inv_max_a=3621.0,
        v_ac_nominal_v=550.0,
        m_max=1.0,
    )
    
    # Define physical cell degradation states
    # Honest: SOH 80%, internal resistance raised to 45 mOhm due to aging
    honest = BatteryState(
        soh_fraction=0.80,
        r_int_ohm=0.045,
        ocv_v=1080.0,
        label="honest_soh_80"
    )
    
    # Inflated: SOH 95%, internal resistance reported as 25 mOhm (validation leakage)
    inflated = BatteryState(
        soh_fraction=0.95,
        r_int_ohm=0.025,
        ocv_v=1095.0,
        label="inflated_soh_95"
    )
    
    # 2. Run simulation sweep over active power export levels
    q_req = 1.5  # Mvar required support
    print(f"Sweeping active power export P (MW) with Q requirement = {q_req} Mvar:")
    print("----------------------------------------------------------------------")
    print("  P (MW)  |  Q_req (Mvar)  |  Q_honest (Mvar)  |  Q_inflated (Mvar)  |  Verdict Flip?")
    print("----------------------------------------------------------------------")
    
    sweep_p = [1.0, 2.0, 2.5, 2.8, 3.0]
    verdict_at_2_8 = None
    
    for p in sweep_p:
        res = compare_honest_vs_inflated(p, q_req, honest, inflated, inv)
        flip_str = "YES" if res["verdict_flips"] else "no"
        print(f"   {p:<5.1f}  |      {q_req:<8.1f}  |       {res['honest_soh']['q_avail_mvar']:<10.3f} |        {res['inflated_soh']['q_avail_mvar']:<11.3f} |      {flip_str}")
        
        if p == 2.8:
            verdict_at_2_8 = res

    print("----------------------------------------------------------------------")
    
    # 3. Assert the Verdict Flip at P = 2.8 MW
    if verdict_at_2_8 is None:
        print("CRITICAL: Verification failed. Swept points did not include P = 2.8 MW.")
        sys.exit(1)
        
    honest_passed = verdict_at_2_8["honest_soh"]["meets_requirement"]
    inflated_passed = verdict_at_2_8["inflated_soh"]["meets_requirement"]
    
    print("\nTarget Verification at P = 2.8 MW:")
    print(f"  - Honest SOH (80%): Q_avail = {verdict_at_2_8['honest_soh']['q_avail_mvar']} Mvar -> Meets requirement ({q_req} Mvar)? {honest_passed}")
    print(f"  - Inflated SOH (95%): Q_avail = {verdict_at_2_8['inflated_soh']['q_avail_mvar']} Mvar -> Meets requirement ({q_req} Mvar)? {inflated_passed}")
    
    if inflated_passed and not honest_passed:
        print("\nSUCCESS: Verdict Flip verified.")
        print("The leakage-inflated SOH predicts compliance (PASS) while the real, aged battery fails in the field (FAIL).")
        print("======================================================================")
        sys.exit(0)
    else:
        print("\nCRITICAL: Verdict Flip conditions not met.")
        print("======================================================================")
        sys.exit(1)

if __name__ == "__main__":
    run_verification()
