# Factory Integration Guide

WaggleDance FACTORY profile provides local-first, auditable decision-making
for industrial environments. No cloud dependency, no API keys.

## Architecture

```
PLC / SCADA / MES / ROS 2
       │
       ▼
   MQTT Broker (Mosquitto)
       │
       ▼
   WaggleDance MQTT Adapter
       │
       ▼
   Solver-first Reasoning
   (OEE calculator, SPC monitor, maintenance predictor)
       │
       ▼
   MAGMA Audit Trail (every decision traceable)
       │
       ▼
   Action Bus (deny-by-default, verified before execution)
```

## Supported Integration Patterns

### 1. MQTT (native, recommended)

WaggleDance includes a full MQTT stack (paho-mqtt, background thread,
asyncio bridge, reconnect, dedup). No additional setup needed.

**Topic convention:**
```
factory/sensors/{equipment_id}/temperature
factory/sensors/{equipment_id}/vibration
factory/sensors/{equipment_id}/oee
factory/alerts/{equipment_id}/{severity}
```

**Example: publish OEE from PLC via Node-RED:**
```json
Topic: factory/sensors/line-1/oee
Payload: {
  "equipment_id": "line-1",
  "oee": 0.72,
  "availability": 0.85,
  "performance": 0.90,
  "quality": 0.94,
  "timestamp": "2026-03-18T14:30:00Z"
}
```

WaggleDance will:
1. Ingest via MQTTSensorIngest
2. Route to OEE solver (solver-first, no LLM needed)
3. Apply SPC rules (3σ alert if out of control)
4. Log to MAGMA audit trail
5. Trigger maintenance prediction if vibration anomaly detected

### 2. ROS 2 (via mqtt_bridge)

If your factory uses ROS 2 nodes (e.g. for cobot coordination or
AGV navigation), use the standard `mqtt_bridge` ROS 2 package:

```bash
# ROS 2 side
sudo apt install ros-${ROS_DISTRO}-mqtt-bridge
```

```yaml
# mqtt_bridge config (bridge_config.yaml)
bridge:
  ros2mqtt:
    - topic: /factory/oee
      mqtt_topic: factory/sensors/line-1/oee
      primitive: true
    - topic: /factory/vibration
      mqtt_topic: factory/sensors/press-1/vibration
      primitive: true
  mqtt2ros:
    - topic: factory/alerts/+/critical
      ros_topic: /waggledance/alerts
      primitive: true
```

WaggleDance does not need to know about ROS 2 — it only sees MQTT messages.
This is intentional: the runtime stays protocol-agnostic.

### 3. OPC-UA (via opcua-mqtt-gateway)

For Siemens S7, Beckhoff TwinCAT, or any OPC-UA enabled PLC:

```bash
# Use open source gateway
pip install asyncua
# Or: https://github.com/eclipse/eclipseiot-testbed-opcua-to-mqtt
```

Map OPC-UA nodes to MQTT topics. WaggleDance handles the rest.

### 4. Modbus (via modbus-mqtt-bridge)

For legacy PLCs without OPC-UA:

```bash
# Use modbus-mqtt bridge
pip install modbus-mqtt
```

Same pattern: Modbus registers → MQTT topics → WaggleDance.

### 5. EV Battery Production Line (example use case)

Gigafactory-style battery cell production requires 100% traceability
for every quality decision. WaggleDance handles this natively:

```
Cell Formation Cycler (Modbus) → modbus-mqtt-bridge
  → factory/sensors/cycler-42/voltage
  → factory/sensors/cycler-42/temperature
  → factory/sensors/cycler-42/capacity_ah

Vision Inspection (ROS 2) → mqtt_bridge
  → factory/sensors/vision-1/defect_count
  → factory/sensors/vision-1/alignment_mm

MES Database (OPC-UA) → opcua-mqtt-gateway
  → factory/sensors/mes/batch_id
  → factory/sensors/mes/yield_pct
```

WaggleDance applies SPC rules to each measurement, grades every
decision (gold/silver/bronze), and stores the full audit trail in
MAGMA. Every cell's quality decision is traceable from raw sensor
reading to final pass/fail — without cloud dependency.

Applicable to: Tesla Gigafactory, BYD, CATL, Northvolt, Samsung SDI,
or any battery/semiconductor/pharma line requiring offline audit.

## Protocol-Agnostic by Design

WaggleDance does not include vendor-specific adapters for Siemens,
ABB, Tesla, or any particular manufacturer. Instead, it speaks MQTT
and relies on standard open-source bridges for protocol translation.

This means:
- Same runtime works in a Finnish paper mill and a Chinese EV factory
- No vendor lock-in — swap the bridge, keep the intelligence
- Compliance teams see one audit system regardless of equipment vendor

Supported equipment ecosystems (via MQTT bridges):
- **European**: Siemens S7/TIA, Beckhoff TwinCAT, ABB AC500, Festo, Wago
- **American**: Allen-Bradley/Rockwell, Tesla Gigafactory systems, Honeywell
- **Chinese**: Delta, Inovance, Huichuan, BYD production systems
- **Japanese**: Mitsubishi MELSEC, Omron, Fanuc, Keyence
- **Universal**: Any system with MQTT, OPC-UA, Modbus TCP/RTU, or REST API

## Built-in Factory Capabilities

| Capability | Type | Description |
|---|---|---|
| OEE Calculator | model_based | Availability × Performance × Quality decomposition |
| SPC Monitor | statistical | Control chart with 3σ alerts, Cpk calculation |
| Maintenance Predictor | model_based | Runtime hours + vibration + temperature delta |
| Downtime Analyzer | retrieval | Root cause lookup from historical data |

All capabilities are configured in `configs/capsules/factory.yaml`.

## MAGMA Audit for Compliance

Every decision is logged with full provenance:
- **What** was decided (action)
- **Why** (which solver, what input data)
- **When** (timestamp, sequence)
- **Confidence** (quality grade: gold/silver/bronze)
- **Verification** (was the result checked before acting)

This is designed for ISO 9001 / IEC 62443 compliance contexts
where every automated decision must be traceable.

## Quick Start

```bash
# 1. Start MQTT broker
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto

# 2. Start WaggleDance with FACTORY profile
python -m waggledance.adapters.cli.start_runtime --profile FACTORY

# 3. Publish test OEE data
mosquitto_pub -t factory/sensors/line-1/oee -m '{"oee": 0.72, "availability": 0.85}'

# 4. Check dashboard
open http://localhost:8000
```
