#!/usr/bin/env python3
"""
pandapower_roundtrip.py — Runs a real pandapower load-flow analysis to demonstrate
the grid-level impact of SOH-aware reactive capability derating.

Exposes the verdict flip:
- Inflated SOH (95%) assumes Q_avail = 1.619 Mvar, allowing successful 1.5 Mvar injection.
- Honest SOH (80%) physically limits Q_avail to 1.152 Mvar, capping injection and
  causing a reactive power shortfall and voltage sag at the BESS bus.
"""

import json
import sys
import pandapower as pp
from soh_capability import InverterSpec, BatteryState, reactive_capability

def run_pandapower_roundtrip():
    print("======================================================================")
    # 1. Define physical specifications (Representative ~3.45 MVA class BESS)
    inv = InverterSpec(
        s_rated_mva=3.45,
        v_dc_nominal_v=1100.0,
        i_inv_max_a=3621.0,
        v_ac_nominal_v=550.0,
        m_max=1.0,
    )
    
    honest = BatteryState(
        soh_fraction=0.80,
        r_int_ohm=0.045,
        ocv_v=1080.0,
        label="honest_soh_80",
        r_int_scatter=0.15
    )
    
    inflated = BatteryState(
        soh_fraction=0.95,
        r_int_ohm=0.025,
        ocv_v=1095.0,
        label="inflated_soh_95",
        r_int_scatter=0.15
    )
    
    # 2. Calculate SOH-aware capabilities
    p_req = 2.8      # Requested MW export
    q_req = 1.5      # Requested Mvar support
    
    cap_honest = reactive_capability(p_req, honest, inv)
    cap_inflated = reactive_capability(p_req, inflated, inv)
    
    q_avail_honest = cap_honest["q_avail_mvar"]
    q_avail_inflated = cap_inflated["q_avail_mvar"]
    
    print("SOH-Aware Reactive Capability Calculations:")
    print(f"  - Requested: P = {p_req} MW, Q = {q_req} Mvar")
    print(f"  - Inflated SOH (95%): Q_avail = {q_avail_inflated:.4f} Mvar (Nominal)")
    print(f"  - Honest SOH (80%): Q_avail = {q_avail_honest:.4f} Mvar (Nominal, Bounds: {cap_honest['q_avail_mvar_min']:.4f} - {cap_honest['q_avail_mvar_max']:.4f} Mvar)")
    print("======================================================================")

    # Helper function to create BESS network
    def create_bess_network(delivered_q):
        net = pp.create_empty_network()
        # Create buses: Bus 0 (Grid Slack at 20 kV), Bus 1 (BESS bus at 0.55 kV / 550 V)
        bus_slack = pp.create_bus(net, vn_kv=20.0, name="Grid Slack")
        bus_bess = pp.create_bus(net, vn_kv=0.55, name="BESS Bus")
        
        # Slack external grid
        pp.create_ext_grid(net, bus=bus_slack, vm_pu=1.0, name="External Grid")
        
        # Add custom BESS step-up transformer std type
        pp.create_std_type(
            net,
            {
                "sn_mva": 3.5,
                "vn_hv_kv": 20.0,
                "vn_lv_kv": 0.55,
                "vkr_percent": 1.0,
                "vk_percent": 6.0,
                "i0_percent": 0.1,
                "pfe_kw": 2.0,
                "shift_degree": 0.0
            },
            name="3.5 MVA BESS Trafo",
            element="trafo"
        )
        
        # Create transformer
        pp.create_transformer(
            net, hv_bus=bus_slack, lv_bus=bus_bess, std_type="3.5 MVA BESS Trafo", name="BESS Step-up"
        )
        
        # Create BESS generator
        pp.create_sgen(net, bus=bus_bess, p_mw=p_req, q_mvar=delivered_q, name="BESS Inverter")
        return net

    # Case A: Paper Sweep (Inflated SOH)
    # The inverter claims Q_avail = 1.619 Mvar, so the full 1.5 Mvar is delivered.
    delivered_q_inflated = min(q_req, q_avail_inflated)
    net_inflated = create_bess_network(delivered_q_inflated)
    pp.runpp(net_inflated, numba=False)
    v_bess_inflated = net_inflated.res_bus.vm_pu.loc[bus_bess := 1]

    # Case B: Field Reality (Honest SOH)
    # The inverter is physically limited to Q_avail = 1.1515 Mvar. The actual injection is capped.
    delivered_q_honest = min(q_req, q_avail_honest)
    net_honest = create_bess_network(delivered_q_honest)
    pp.runpp(net_honest, numba=False)
    v_bess_honest = net_honest.res_bus.vm_pu.loc[bus_bess := 1]
    
    # Case C: Honest SOH Worst-Case (R_int max / Q min)
    delivered_q_honest_worst = min(q_req, cap_honest["q_avail_mvar_min"])
    net_worst = create_bess_network(delivered_q_honest_worst)
    pp.runpp(net_worst, numba=False)
    v_bess_worst = net_worst.res_bus.vm_pu.loc[bus_bess := 1]

    # Results Comparison Table
    shortfall_honest = max(0.0, q_req - delivered_q_honest)
    shortfall_worst = max(0.0, q_req - delivered_q_honest_worst)
    
    print("\nGRID-LEVEL POWER FLOW RESULTS COMPARISON:")
    print("------------------------------------------------------------------------------------------------")
    print("  Scenario             |  Req Q (Mvar)  |  Deliv Q (Mvar)  |  Q Shortfall  |  BESS Bus Volt (p.u.)")
    print("------------------------------------------------------------------------------------------------")
    print(f"  Inflated SOH (95%)   |      {q_req:<8.2f}  |      {delivered_q_inflated:<11.3f} |      0.000    |      {v_bess_inflated:<18.4f}")
    print(f"  Honest SOH (80% Nom) |      {q_req:<8.2f}  |      {delivered_q_honest:<11.3f} |      {shortfall_honest:<8.3f} |      {v_bess_honest:<18.4f}")
    print(f"  Honest SOH (80% Min) |      {q_req:<8.2f}  |      {delivered_q_honest_worst:<11.3f} |      {shortfall_worst:<8.3f} |      {v_bess_worst:<18.4f}")
    print("------------------------------------------------------------------------------------------------")
    
    # Verdict verification
    verdict_flip = (delivered_q_inflated >= q_req) and (delivered_q_honest < q_req)
    if verdict_flip:
        print("\nVERDICT FLIP CONFIRMED IN POWER FLOW:")
        print("  On paper (inflated SOH), the BESS meets the grid-code reactive power requirement.")
        print(f"  In reality (honest SOH), physical modulation limits cause a {shortfall_honest:.3f} Mvar shortfall.")
        print(f"  This shortfall sags the local terminal voltage from {v_bess_inflated:.4f} p.u. down to {v_bess_honest:.4f} p.u.")
        print(f"  Under worst-case R_int scatter, the shortfall increases to {shortfall_worst:.3f} Mvar, sagging voltage to {v_bess_worst:.4f} p.u.")
        print("======================================================================")
        
        # Save results to json
        res_json = {
            "requested_p_mw": p_req,
            "requested_q_mvar": q_req,
            "inflated_scenario": {
                "delivered_q_mvar": round(delivered_q_inflated, 4),
                "q_shortfall_mvar": 0.0,
                "bus_voltage_pu": round(v_bess_inflated, 5)
            },
            "honest_nominal_scenario": {
                "delivered_q_mvar": round(delivered_q_honest, 4),
                "q_shortfall_mvar": round(shortfall_honest, 4),
                "bus_voltage_pu": round(v_bess_honest, 5)
            },
            "honest_worst_scenario": {
                "delivered_q_mvar": round(delivered_q_honest_worst, 4),
                "q_shortfall_mvar": round(shortfall_worst, 4),
                "bus_voltage_pu": round(v_bess_worst, 5)
            },
            "verdict_flip": True
        }
        with open("pandapower_results.json", "w") as f:
            json.dump(res_json, f, indent=2)
            
        sys.exit(0)
    else:
        print("\nCRITICAL: Verdict Flip not observed in power flow.")
        print("======================================================================")
        sys.exit(1)

if __name__ == "__main__":
    run_pandapower_roundtrip()
