#!/usr/bin/env python3
"""
WaggleDance Benchmark v3 -Agent Persona & Difficulty Grading
=============================================================
64 questions across 8 agent personas + cross-agent + hallucination traps.
Each question has verified factual answers, difficulty grading, and keyword scoring.

Usage:
  python benchmark_v3.py                     # Full run
  python benchmark_v3.py --agent tarhaaja    # Single agent
  python benchmark_v3.py --difficulty hard   # Only hard+expert
  python benchmark_v3.py --dry-run           # Print questions without running
  python benchmark_v3.py --model phi4-mini   # Override model
"""

import requests
import time
import json
import sys
import re
import argparse
from datetime import datetime
from collections import defaultdict

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "phi4-mini"

# Agent persona system prompts for richer, more specific responses
AGENT_PERSONAS = {
    "tarhaaja": "You are an expert beekeeper (tarhaaja) with decades of experience in Finnish conditions. Always include specific numbers, thresholds, and practical recommendations. Mention relevant kg amounts, day counts, temperatures, and percentages.",
    "tautivahti": "You are a bee disease specialist (tautivahti). Always include specific disease names, treatment protocols, thresholds, and diagnostic criteria. Be precise about chemicals, dosages, and timing.",
    "meteorologi": "You are a meteorologist specializing in weather impacts on beekeeping. Include specific temperatures, wind speeds, precipitation amounts, and seasonal timing.",
    "hortonomi": "You are a horticulturist (hortonomi) specializing in bee-relevant plants and nectar flows. Include specific plant species, bloom times, and nectar yields.",
    "tutkija": "You are a bee researcher (tutkija). Include scientific names, research findings, and evidence-based recommendations.",
    "ekonomisti": "You are an agricultural economist. Include specific costs, yields, ROI calculations, and market data.",
    "rakentaja": "You are a construction and building specialist. Include specific materials, measurements, building codes, and safety standards.",
    "energianeuvoja": "You are an energy advisor. Include specific kWh values, costs per unit, efficiency ratings, and energy calculations.",
    "cross_agent": "You are a multi-domain expert covering beekeeping, property management, weather, and energy. Provide comprehensive answers covering all relevant domains with specific numbers and actionable priorities.",
    "hallucination_trap": "You are a careful, factual expert. If a question contains a false premise, clearly state that the premise is wrong. Never make up facts. Include specific numbers and standards where relevant.",
}

# ══════════════════════════════════════════════════════════════════
# Reusable functions (from benchmark_v2.py)
# ══════════════════════════════════════════════════════════════════

def ollama_generate(model, prompt, system=None, timeout=60, num_predict=300):
    """Generate response from Ollama."""
    try:
        start = time.perf_counter()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": num_predict, "num_ctx": 2048}
        }
        if system:
            payload["system"] = system
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=timeout
        )
        elapsed = time.perf_counter() - start
        data = resp.json()
        text = data.get("response", "").strip()
        tokens = data.get("eval_count", 0)
        return {
            "text": text,
            "time_s": round(elapsed, 2),
            "tokens": tokens,
            "tok_s": round(tokens / max(elapsed, 0.01), 1),
            "error": None
        }
    except Exception as e:
        return {"text": "", "time_s": 0, "tokens": 0, "tok_s": 0, "error": str(e)[:80]}


def evaluate(response_text, keywords):
    """Score response by keyword hits.

    Supports OR syntax: "enough|sufficient|adequate" counts as one keyword
    that matches if ANY alternative is found in the response.
    """
    text_lower = response_text.lower()
    hits = 0
    for kw in keywords:
        alternatives = [alt.strip().lower() for alt in kw.split("|")]
        if any(alt in text_lower for alt in alternatives):
            hits += 1
    score = hits / max(len(keywords), 1)
    return {"score": round(score, 2), "hits": f"{hits}/{len(keywords)}", "pass": score >= 0.3}


def strip_thinking(text):
    """Remove <think>...</think> blocks from response for keyword scoring."""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return cleaned if cleaned else text


# ══════════════════════════════════════════════════════════════════
# AGENT TESTS -52 questions across 8 agent personas
# ══════════════════════════════════════════════════════════════════

AGENT_TESTS = {
    "tarhaaja": [
        {
            "q": "You open a hive in late May and see 6 frames of capped brood, 2 frames of honey, and the queen is laying well. Is this colony ready for a honey super?",
            "expected_keywords": ["super", "strong|ready|yes", "frames|frame", "brood", "add|place|put", "space|swarm|room"],
            "correct_answer": "Yes. A colony with 6+ frames of brood and active laying in late May is strong enough for a honey super. Adding space prevents swarming impulse.",
            "agent": "tarhaaja",
            "difficulty": "easy",
            "category": "colony_management",
        },
        {
            "q": "During a July inspection you find 5 open queen cells on the bottom of frames. The existing queen is still present. What is happening and what should you do?",
            "expected_keywords": ["swarm", "queen cell|queen cup", "split|divide|replac", "prevent|manage|control", "remove|destroy|cut", "nuc|nucleus"],
            "correct_answer": "The colony is preparing to swarm. Options: remove queen cells (temporary), make a split/nuc with the old queen, or do an artificial swarm. Simply destroying cells delays swarming by ~7 days but doesn't solve the urge.",
            "agent": "tarhaaja",
            "difficulty": "medium",
            "category": "colony_management",
        },
        {
            "q": "How many days does it take for a worker bee to develop from egg to emergence?",
            "expected_keywords": ["21", "day", "egg", "larva|larval", "pupa|pupal|capped"],
            "correct_answer": "21 days total: 3 days egg, 6 days larva, 12 days pupa.",
            "agent": "tarhaaja",
            "difficulty": "easy",
            "category": "bee_biology",
        },
        {
            "q": "A colony has 18 kg of honey stores in late September in central Finland. Is this enough for winter?",
            "expected_keywords": ["15|20", "kg|kilogram", "enough|sufficient|adequate|within|range", "winter", "stores|reserve|food|honey", "feed|margin|supplement|top"],
            "correct_answer": "18 kg is within the recommended 15-20 kg range for Finnish zones II-III. The colony should survive winter, though topping up to 20 kg provides a safety margin.",
            "agent": "tarhaaja",
            "difficulty": "medium",
            "category": "seasonal_management",
        },
        {
            "q": "What is the minimum flight temperature for honeybees?",
            "expected_keywords": ["10|12", "degree|celsius|C", "temperature|temp", "flight|fly|forag"],
            "correct_answer": "Honeybees require a minimum of 10-12 degrees Celsius for flight. Below this, they remain clustered in the hive.",
            "agent": "tarhaaja",
            "difficulty": "easy",
            "category": "bee_biology",
        },
        {
            "q": "You extract honey and the refractometer reads 19.5% moisture. Can you jar this honey?",
            "expected_keywords": ["18|18%", "moisture|water content", "high|exceed|above|over", "no|not|cannot|shouldn't", "ferment|spoil|degrade", "dry|dehumidif|reduce|lower|blend"],
            "correct_answer": "No. Maximum moisture for extraction/jarring is 18%. At 19.5% the honey will likely ferment. Use a honey dehumidifier or blend with drier honey to bring moisture below 18%.",
            "agent": "tarhaaja",
            "difficulty": "medium",
            "category": "honey_harvest",
        },
        {
            "q": "How long does a queen bee develop from egg to emergence, and how does this compare to workers and drones?",
            "expected_keywords": ["16", "21", "24", "queen", "worker", "drone", "day|development"],
            "correct_answer": "Queen: 16 days, Worker: 21 days, Drone: 24 days. Queens develop fastest due to royal jelly diet and larger cell.",
            "agent": "tarhaaja",
            "difficulty": "hard",
            "category": "bee_biology",
        },
        {
            "q": "A Finnish beekeeper with 10 hives wants to estimate their season's yield. What is a reasonable expectation and what factors affect it most?",
            "expected_keywords": ["30|50|300|500", "kg|kilogram", "hive|colony", "weather|climate|season", "nectar|bloom|flower", "flow|yield|harvest", "location|area|region"],
            "correct_answer": "Finnish average is 30-50 kg per hive per year. Key factors: local nectar flow timing, weather during bloom, colony strength, and proximity to flowering sources. 10 hives could yield 300-500 kg in a good season.",
            "agent": "tarhaaja",
            "difficulty": "hard",
            "category": "honey_harvest",
        },
    ],

    "tautivahti": [
        {
            "q": "You see sunken, perforated cappings on brood frames and detect a sour smell. When you poke a dead larva with a matchstick, it stretches into a ropy string over 2 cm long. What disease is this?",
            "expected_keywords": ["foulbrood", "AFB", "american", "ropy", "paenibacillus", "burn|destroy|incinerat"],
            "correct_answer": "American Foulbrood (AFB), caused by Paenibacillus larvae. The ropy test (>2.5 cm string) is diagnostic. AFB is a notifiable disease; infected equipment must typically be burned.",
            "agent": "tautivahti",
            "difficulty": "easy",
            "category": "disease_diagnosis",
        },
        {
            "q": "What is the varroa treatment threshold in August, and how do you measure it?",
            "expected_keywords": ["3", "mite", "100", "bee", "wash|roll|sample", "alcohol|sugar|ether", "threshold"],
            "correct_answer": "Treatment threshold is 3 mites per 100 bees via alcohol wash (or 1% infestation). Natural mite fall on a sticky board can also indicate: >10 mites/day in summer means treat. August treatment is critical before winter bees are raised.",
            "agent": "tautivahti",
            "difficulty": "easy",
            "category": "varroa_management",
        },
        {
            "q": "When and how should oxalic acid be applied for varroa treatment in Finland?",
            "expected_keywords": ["broodless|low activity|less active", "winter", "sublim|vaporiz", "trickle|drip|drizzle|solution", "oxalic", "december|november|late autumn|evening"],
            "correct_answer": "Apply during the broodless period (late November-December in Finland). Methods: sublimation (vaporization) or trickle (3.2% solution drizzled over cluster). Effective even below 5 degrees C if cluster is accessible. Treats only phoretic mites, so broodless timing is key.",
            "agent": "tautivahti",
            "difficulty": "medium",
            "category": "varroa_management",
        },
        {
            "q": "You notice scattered brood pattern, some larvae are twisted in cells and turning yellow-brown. There's no ropy test and no smell. What could this be?",
            "expected_keywords": ["european", "foulbrood", "EFB", "melissococcus|plutonius", "scattered|patchy|irregular|twisted", "brood|larvae"],
            "correct_answer": "Likely European Foulbrood (EFB) caused by Melissococcus plutonius. Key differences from AFB: no ropy test, larvae die before capping (twisted/displaced), yellowish color, sour but not foul smell. Often resolves with requeening and strong nectar flow.",
            "agent": "tautivahti",
            "difficulty": "medium",
            "category": "disease_diagnosis",
        },
        {
            "q": "A colony shows white flecks on bee bodies, some bees have deformed wings, and you see mites on drone pupae. What is your treatment plan?",
            "expected_keywords": ["varroa", "deformed", "wing", "treat", "formic|organic", "oxalic", "strip|apivar|amitraz", "thymol"],
            "correct_answer": "Heavy varroa infestation with Deformed Wing Virus (DWV). Immediate treatment needed: formic acid (MAQS/FormicPro) during brood season, or amitraz strips (Apivar). Follow up with oxalic acid in broodless period. The colony may be weakened and need combining if population drops severely.",
            "agent": "tautivahti",
            "difficulty": "hard",
            "category": "varroa_management",
        },
        {
            "q": "You see chalk-white mummified larvae at the hive entrance and scattered on the bottom board. What is the condition and how serious is it?",
            "expected_keywords": ["chalkbrood|chalk", "ascosphaera|fungus|fung", "mummif", "ventilat", "requeen|replace queen|hygienic", "stress|weak|damp|moisture"],
            "correct_answer": "Chalkbrood, caused by the fungus Ascosphaera apis. Mummified larvae (white to dark grey) are ejected by house bees. Usually a stress-related condition. Treatment: improve ventilation, reduce moisture, requeen with hygienic stock. Rarely fatal but weakens colony.",
            "agent": "tautivahti",
            "difficulty": "medium",
            "category": "disease_diagnosis",
        },
        {
            "q": "What is the correct brood nest temperature range, and what happens if it deviates significantly?",
            "expected_keywords": ["34|35", "36|temperature", "brood", "development", "defect|abnormal|problem"],
            "correct_answer": "Brood nest temperature is 34.5-35.5 degrees C (plus/minus 0.5). Below 34 C: slow development, increased susceptibility. Above 36 C: developmental defects, larval death. Bees regulate via fanning (cooling) and clustering (heating).",
            "agent": "tautivahti",
            "difficulty": "hard",
            "category": "colony_health",
        },
        {
            "q": "A beekeeper reports that bees are crawling on the ground, unable to fly, with bloated abdomens and dysentery stains on frames. It's early March. Diagnosis?",
            "expected_keywords": ["nosema", "dysentery", "apis", "ceranae|apis", "winter", "crawl|crawling|unable to fly", "confine"],
            "correct_answer": "Likely Nosema infection (N. apis or N. ceranae), exacerbated by long winter confinement. Symptoms: dysentery (fecal staining), crawling bees, bloated abdomens, inability to fly. Treatment: ensure cleansing flights when possible, feed syrup with Fumidil-B (where legal), requeen.",
            "agent": "tautivahti",
            "difficulty": "expert",
            "category": "disease_diagnosis",
        },
    ],

    "meteorologi": [
        {
            "q": "It's mid-June in Finland, forecast shows 5 consecutive days above 20 degrees C and sunny. What does this mean for beekeeping?",
            "expected_keywords": ["nectar", "flow", "forage|food|resource", "honey", "super", "strong|active|peak", "bloom|flower|blossom"],
            "correct_answer": "Excellent conditions for a strong nectar flow. Warm sustained temperatures promote flower blooming and nectar secretion. Beekeepers should ensure hives have enough supers for honey storage and check for swarming.",
            "agent": "meteorologi",
            "difficulty": "easy",
            "category": "weather_beekeeping",
        },
        {
            "q": "A cold snap is forecast: 3 nights below -5 degrees C in early April. Bees have started flying. What risks does this pose?",
            "expected_keywords": ["brood", "chill|cold|frost", "cluster", "stores|reserve|food|supply", "starvation|starv|hunger|consum", "protect|shelter|insulate|relocat|indoor"],
            "correct_answer": "Risk of brood chilling if cluster contracts and exposes brood. Increased stores consumption. Bees cannot forage. Ensure sufficient feed (emergency fondant). Do not open hives. Colonies may lose early brood, delaying spring buildup by 1-2 weeks.",
            "agent": "meteorologi",
            "difficulty": "medium",
            "category": "weather_beekeeping",
        },
        {
            "q": "Heavy rain is forecast for 10 consecutive days in July during peak linden bloom. Impact on honey production?",
            "expected_keywords": ["reduce|decreas|diminish", "nectar|honey", "wash", "forage|activity|pollination", "loss|lost|lower|decline|cessation", "harvest", "delay"],
            "correct_answer": "Significant negative impact. Rain washes nectar from flowers, prevents bee foraging flights, and can cause starvation in strong colonies consuming stores. Linden bloom window is short (2-3 weeks); 10 days of rain could mean losing 50-70% of the linden crop.",
            "agent": "meteorologi",
            "difficulty": "hard",
            "category": "weather_beekeeping",
        },
        {
            "q": "Wind forecast shows sustained 15 m/s winds with gusts to 22 m/s for a rural property. What precautions are needed?",
            "expected_keywords": ["secure", "damage|topple|dislodge|collaps|harm", "tree", "power", "outage|blackout|cut", "loose|debris|fallen|blow|fall", "gust"],
            "correct_answer": "Secure loose objects, check roof and fence integrity, ensure backup power for critical systems. Trees may drop branches. Gusts over 20 m/s can cause structural damage. Keep vehicles sheltered. Power outages likely in rural areas.",
            "agent": "meteorologi",
            "difficulty": "medium",
            "category": "weather_property",
        },
        {
            "q": "Temperature forecast: -25 degrees C for the next 5 days. A remote cottage has been unoccupied for 2 weeks. Priority concerns?",
            "expected_keywords": ["pipe", "freez|frost", "burst|crack|rupture|break|damage", "heat|warm", "water|ice", "insulate|protect|shelter", "drain"],
            "correct_answer": "Top priority: water pipes freezing and bursting. At -25 C, even insulated pipes are at risk without heating. If no remote heating control, pipes should have been drained. Check that backup heating is running. Frozen pipes can cause massive water damage when thawing.",
            "agent": "meteorologi",
            "difficulty": "hard",
            "category": "weather_property",
        },
        {
            "q": "Late August, nighttime temperatures dropping to 5 degrees C, daytime 15 C. How does this affect the bee colony's winter preparation?",
            "expected_keywords": ["winter", "bee", "brood", "reduce|decreas|slow", "cluster", "preparation|prepar|ready|transition", "feed|store|forag"],
            "correct_answer": "Colony transitions to winter mode: queen reduces laying, workers shift to long-lived winter bees (vitellogenin-rich). Foraging drops. This is the critical window for varroa treatment and supplemental feeding. Ensure 15-20 kg stores before sustained cold arrives.",
            "agent": "meteorologi",
            "difficulty": "medium",
            "category": "weather_beekeeping",
        },
    ],

    "sahkoagentti": [
        {
            "q": "Electricity spot price is 0.45 EUR/kWh now but drops to 0.03 EUR/kWh between midnight and 6 AM. A 2000W water heater needs 3 hours. Cost difference?",
            "expected_keywords": ["2.70", "0.18", "save", "night|off-peak|midnight", "cheap|cheaper|less|lower", "wait|delay|shift", "cost|EUR|price"],
            "correct_answer": "Daytime cost: 2kW * 3h * 0.45 = 2.70 EUR. Night cost: 2kW * 3h * 0.03 = 0.18 EUR. Saving of 2.52 EUR per heating cycle by shifting to night hours.",
            "agent": "sahkoagentti",
            "difficulty": "easy",
            "category": "energy_cost",
        },
        {
            "q": "A household runs: fridge (150W continuous), 3 LED lights (10W each), TV (80W), and an electric sauna (9kW). What is the total load in kW?",
            "expected_keywords": ["9.26|9.3|9260", "kw|kilowatt|watt", "load", "sauna"],
            "correct_answer": "150W + 30W + 80W + 9000W = 9260W = 9.26 kW. The sauna dominates at 97% of total load. Main fuse capacity (typically 25A at 230V = 5.75kW single phase) may be insufficient; sauna usually needs 3-phase connection.",
            "agent": "sahkoagentti",
            "difficulty": "medium",
            "category": "load_calculation",
        },
        {
            "q": "A 300W solar panel in Finland produces an average of 800 kWh per year per kWp installed. How many panels needed to offset a household using 5000 kWh per year?",
            "expected_keywords": ["6|seven|7", "panel", "1.8|2", "kWp|kilowatt|kwp", "solar"],
            "correct_answer": "Each 300W panel = 0.3 kWp. At 800 kWh/kWp/year, each panel produces 240 kWh/year. For 5000 kWh: 5000/240 = 20.8, so approximately 21 panels (6.3 kWp). However, self-consumption rate matters; without battery storage, effective offset is 30-50%.",
            "agent": "sahkoagentti",
            "difficulty": "hard",
            "category": "energy_cost",
        },
        {
            "q": "The main fuse is 3x25A. Can you simultaneously run an electric sauna (9kW, 3-phase), oven (3kW), and EV charger (11kW, 3-phase)?",
            "expected_keywords": ["no", "overload", "fuse", "23|exceed", "kw", "17|trip|overload"],
            "correct_answer": "Total load: 9kW + 3kW + 11kW = 23kW. Maximum capacity: 3 x 25A x 230V = 17.25 kW. Load exceeds capacity by ~6kW. The main fuse will trip. Solution: stagger usage or upgrade to 3x35A.",
            "agent": "sahkoagentti",
            "difficulty": "medium",
            "category": "load_calculation",
        },
        {
            "q": "An electric underfloor heating system in a 20 square meter bathroom uses 150W per square meter. Monthly cost at average 0.12 EUR/kWh if running 8 hours daily?",
            "expected_keywords": ["3000|3kW|3 kw", "72|kWh", "8.64|EUR|cost", "month"],
            "correct_answer": "Total power: 20 m2 * 150 W/m2 = 3000W = 3kW. Daily energy: 3kW * 8h = 24 kWh. Monthly: 24 * 30 = 720 kWh. Cost: 720 * 0.12 = 86.40 EUR per month. Consider thermostat programming to reduce runtime.",
            "agent": "sahkoagentti",
            "difficulty": "hard",
            "category": "energy_cost",
        },
        {
            "q": "Power outage detected. The UPS shows 30 minutes battery remaining. Which devices should stay powered: server (500W), fridge (150W), security cameras (50W), or gaming PC (600W)?",
            "expected_keywords": ["fridge", "security", "camera", "priorit", "server", "essential"],
            "correct_answer": "Priority: 1) Security cameras (50W) -safety critical. 2) Fridge (150W) -food preservation. 3) Server (500W) -only if running critical services. Drop gaming PC (non-essential). With 200W load (cameras + fridge), UPS could last ~75 minutes instead of 30.",
            "agent": "sahkoagentti",
            "difficulty": "medium",
            "category": "energy_management",
        },
    ],

    "kotiavustaja": [
        {
            "q": "CO sensor reads 35 ppm in the kitchen. The gas stove has been on for 20 minutes. What action is needed?",
            "expected_keywords": ["ventilat", "open", "window", "danger", "25", "ppm", "evacuate", "stove"],
            "correct_answer": "35 ppm exceeds the 25 ppm safe limit (8h TWA). Immediate actions: turn off gas stove, open windows for ventilation, evacuate if levels rise above 50 ppm. If headaches or nausea, evacuate immediately. Check for faulty combustion or blocked flue.",
            "agent": "kotiavustaja",
            "difficulty": "easy",
            "category": "safety",
        },
        {
            "q": "Motion detected at the back door at 2:30 AM. No residents expected home. Front door camera shows no activity. What to do?",
            "expected_keywords": ["alert|report|warning", "camera|surveillance", "record|capture|footage", "notify|inform|contact|verify|check", "alarm|security|suspicious", "police|law enforcement|authorit", "light"],
            "correct_answer": "Activate back door camera recording, turn on exterior lights (deterrent), send alert notification to owner. If person lingers or attempts entry, trigger alarm and notify authorities. Check if it could be wildlife (common false positive).",
            "agent": "kotiavustaja",
            "difficulty": "medium",
            "category": "security",
        },
        {
            "q": "Indoor temperature is 16 degrees C, thermostat set to 21 C. Outdoor temperature is -10 C. The heat pump shows an error code. What should happen?",
            "expected_keywords": ["backup", "heat", "auxiliary|backup|alternative", "error|malfunct|incorrect|fault", "repair|service|fix|upgrad", "electric", "supplement|secondary|extra"],
            "correct_answer": "Temperature is 5 degrees below target and dropping. Switch to backup/auxiliary heating (electric radiators). Schedule heat pump service. At -10 C outdoor, the house will cool rapidly. Monitor pipe freeze risk if temperature continues dropping.",
            "agent": "kotiavustaja",
            "difficulty": "medium",
            "category": "climate_control",
        },
        {
            "q": "Water leak sensor triggered in the bathroom. Water meter shows continuous flow of 5 liters per minute. Nobody is using water. Emergency response?",
            "expected_keywords": ["shut|close|stop|contact", "valve", "water|leak|leakage|burst", "main", "off", "damage", "plumber"],
            "correct_answer": "Immediate: shut off main water valve (or bathroom shutoff if accessible). 5 L/min = 300 L/hour -serious leak. Call emergency plumber. Move valuables away from water. Turn off electricity to affected area if water near outlets. Document damage for insurance.",
            "agent": "kotiavustaja",
            "difficulty": "hard",
            "category": "safety",
        },
        {
            "q": "Humidity in the basement reads 78%. Normal range is 40-60%. Temperature is 15 C. Concern?",
            "expected_keywords": ["mold", "dehumidif|dehumid", "ventilat", "moisture|humidity|humid", "high|above|exceed|elevat", "risk", "damp|wet|excess"],
            "correct_answer": "78% humidity at 15 C is a mold risk. Mold growth begins at 70%+ sustained humidity. Run a dehumidifier, improve ventilation, check for water intrusion sources. Inspect for existing mold growth. Long-term: consider mechanical ventilation system.",
            "agent": "kotiavustaja",
            "difficulty": "medium",
            "category": "climate_control",
        },
        {
            "q": "The home automation system detects: exterior door open for 15 minutes, indoor temp dropping from 21 to 18 C, it's -20 C outside. Appropriate response?",
            "expected_keywords": ["close", "door", "alert", "heat", "notify", "energy", "loss"],
            "correct_answer": "Send immediate alert to residents -door likely left open accidentally. If no response in 2 minutes, sound interior alarm. Boost heating to compensate. At -20 C, rapid heat loss could freeze exposed pipes. Calculate: 3 degrees drop in 15 minutes means ~12 C/hour cooling rate.",
            "agent": "kotiavustaja",
            "difficulty": "hard",
            "category": "safety",
        },
    ],

    "mokkiavustaja": [
        {
            "q": "Remote cottage water pipe temperature reads -2 degrees C. Nobody has visited in 3 weeks. The cottage is in central Finland. What's the risk?",
            "expected_keywords": ["freez", "pipe", "burst", "heat", "drain", "water", "damage"],
            "correct_answer": "Critical freeze risk. At -2 C, pipes are at or below freezing point. Sustained sub-zero temperatures will cause ice buildup and pipe bursting. Remote action: activate electric trace heating if available. If not, the pipes should have been drained before leaving. Expect damage if already frozen.",
            "agent": "mokkiavustaja",
            "difficulty": "easy",
            "category": "winterization",
        },
        {
            "q": "Sauna stove has been on for 6 hours. Internal temperature is 105 degrees C. The sauna is a traditional wood sauna at the cottage. Assessment?",
            "expected_keywords": ["long|extended|prolonged|hour", "risk", "fire", "overheat|excessive|too hot", "turn off|shut|stop|switch off", "check|monitor|inspect", "safe|danger|hazard"],
            "correct_answer": "6 hours is excessively long for a wood sauna. Normal session: 1-3 hours. At 105 C, fire risk increases -check chimney and surrounding wood structures for heat damage. Turn off/let fire die out. Inspect for smoldering. Possible forgotten stove -common cottage fire cause.",
            "agent": "mokkiavustaja",
            "difficulty": "medium",
            "category": "safety",
        },
        {
            "q": "Spring visit to the cottage after 5 months of winter. Checklist of critical items to inspect before turning on water and electricity?",
            "expected_keywords": ["pipe", "roof", "water", "electric", "mouse", "mold", "inspect", "leak"],
            "correct_answer": "Checklist: 1) Inspect roof for snow/ice damage. 2) Check for animal/mouse intrusion (droppings, nests, chewed wires). 3) Inspect all pipes before turning on water -look for cracks. 4) Open main water slowly and check each joint. 5) Check electrical panel for tripped breakers. 6) Inspect for mold/moisture. 7) Test smoke and CO detectors.",
            "agent": "mokkiavustaja",
            "difficulty": "medium",
            "category": "seasonal_maintenance",
        },
        {
            "q": "The cottage has a well pump. Water comes out brown after being unused for 4 months. Safe to drink?",
            "expected_keywords": ["flush|drain|rinse", "test", "bacteria", "iron|rust|brown|discolor", "sediment|deposit|buildup|stagna", "boil|treat|filter", "not safe|unsafe|don't drink"],
            "correct_answer": "Not safe to drink immediately. Brown water indicates iron/sediment buildup or well contamination. Flush the system thoroughly (run for 15-30 minutes). Get water tested for bacteria (coliforms, E. coli) before drinking. Boil water as precaution until test results confirm safety.",
            "agent": "mokkiavustaja",
            "difficulty": "hard",
            "category": "water_systems",
        },
        {
            "q": "Remote monitoring shows cottage indoor temperature dropped from 8 degrees C to 2 C in 24 hours. Outdoor temperature is -15 C. What happened?",
            "expected_keywords": ["heat", "fail|failure|malfunction", "power", "outage", "backup|emergency|alternative", "freez|frost|ice|cold", "pipe"],
            "correct_answer": "Heating system failure -likely power outage or heater malfunction. At this cooling rate, cottage will reach freezing within ~12 hours. Critical risk: water pipes freeze. Remote actions: check power remotely, activate backup heating. If no remote control, dispatch someone immediately. Priority: drain water system if temperature will reach 0 C.",
            "agent": "mokkiavustaja",
            "difficulty": "hard",
            "category": "emergency",
        },
        {
            "q": "Cottage electricity consumption has been 0 kWh for the last 48 hours. The cottage should have a base load of 50W (fridge, monitoring). What does this indicate?",
            "expected_keywords": ["power", "outage|fault|disconnect", "failure|fail|problem|prevent", "check|monitor|record|measur", "supply|grid|consum|draw", "fuse", "trip|tripped|blow"],
            "correct_answer": "Power supply failure. Zero consumption with expected base load means either: 1) Power outage (check utility status), 2) Main fuse tripped, 3) Supply line damage (tree on wire). Fridge contents at risk after 24h. Monitoring system on battery backup. Contact local electrician or neighbor to check.",
            "agent": "mokkiavustaja",
            "difficulty": "medium",
            "category": "energy_monitoring",
        },
    ],

    "tehdas_valvoja": [
        {
            "q": "Motor bearing temperature is 82 degrees C. Normal operating range is 40-70 C. Vibration has increased 30% over the past 4 hours. Diagnosis and action?",
            "expected_keywords": ["bearing", "fail|failure", "replac", "overheat", "stop", "maintenance", "grease|lubricat"],
            "correct_answer": "Bearing degradation in progress. 82 C is above normal (max 70 C) and rising vibration indicates mechanical wear. Schedule immediate maintenance stop. If trend continues (+3 C/hour), bearing failure within hours. Prepare replacement bearing. Do not wait -catastrophic failure causes extended downtime.",
            "agent": "tehdas_valvoja",
            "difficulty": "medium",
            "category": "predictive_maintenance",
        },
        {
            "q": "Production line speed is 120 units/hour. Quality check shows 8% defect rate. Normal defect rate is 2%. What action is needed?",
            "expected_keywords": ["slow", "reduce|decrease|lower|corrective", "speed", "quality", "inspect|investigat|identify|analyz", "stop|halt|pause|adjust", "defect", "root"],
            "correct_answer": "Defect rate 4x above normal -reduce line speed or stop for inspection. Root cause analysis: check material batch, machine calibration, tool wear, operator changes. At 8% defect rate, continuing produces waste. 120 units/h * 8% = 9.6 defective units/hour. Stop production until cause identified.",
            "agent": "tehdas_valvoja",
            "difficulty": "medium",
            "category": "quality_control",
        },
        {
            "q": "Compressor discharge pressure is 12 bar. Maximum rated pressure is 10 bar. The pressure relief valve should activate at 11 bar but hasn't. Risk assessment?",
            "expected_keywords": ["critical", "danger", "relief|safety valve|PRV", "valve", "fail|failure|rupture", "stop", "over", "pressure"],
            "correct_answer": "CRITICAL situation. Pressure 20% above maximum rating AND the safety relief valve has failed. Immediate emergency stop of compressor. Evacuate area -vessel rupture risk. Do not attempt to release pressure manually. Lock out/tag out. The relief valve must be replaced and tested before restart.",
            "agent": "tehdas_valvoja",
            "difficulty": "hard",
            "category": "safety_monitoring",
        },
        {
            "q": "A CNC machine's spindle shows periodic vibration at exactly 2x the rotation frequency. The tool was changed 2 hours ago. What does this suggest?",
            "expected_keywords": ["imbalance|unbalanc|balance", "tool", "alignment", "mount", "concentr", "runout|run-out|wobble"],
            "correct_answer": "2x rotation frequency vibration typically indicates misalignment or tool imbalance. Since it started after tool change, likely causes: improper tool mounting, tool holder not seated correctly, or tool runout. Stop and re-mount the tool, check concentricity with a dial indicator.",
            "agent": "tehdas_valvoja",
            "difficulty": "expert",
            "category": "predictive_maintenance",
        },
        {
            "q": "Factory energy consumption has increased 25% over the past month with no production increase. Where to look first?",
            "expected_keywords": ["leak|excess", "compress", "air", "hvac", "insulation", "motor|equipment|machine", "efficien|waste|loss"],
            "correct_answer": "Top suspects: 1) Compressed air leaks (most common -a single 3mm leak wastes 4kW). 2) HVAC running inefficiently (clogged filters, faulty controls). 3) Motor degradation (bearing wear increases consumption). 4) Insulation damage. Start with compressed air audit -accounts for 20-30% of industrial electricity use.",
            "agent": "tehdas_valvoja",
            "difficulty": "hard",
            "category": "energy_efficiency",
        },
        {
            "q": "Conveyor belt tension sensor shows 15% drop in tension over the last week. Belt is running but no visible slippage yet. When to act?",
            "expected_keywords": ["adjust", "tension", "prevent|before|avoid", "slip", "schedule|immediate|prompt", "maintenance|inspect|monitor|address", "wear|degrad|deteriorat|worsen|damage"],
            "correct_answer": "Act now during planned downtime -do not wait for slippage. 15%/week tension loss indicates belt stretching or tensioner wear. Slippage causes uneven wear, product misalignment, and eventual belt failure. Schedule tensioner adjustment within 2-3 days. Inspect belt for wear, check rollers for bearing issues.",
            "agent": "tehdas_valvoja",
            "difficulty": "medium",
            "category": "predictive_maintenance",
        },
    ],

    "tutkija": [
        {
            "q": "What is the melting point of beeswax in both Celsius and Kelvin?",
            "expected_keywords": ["62", "63", "64", "65", "335", "336", "337", "338", "kelvin", "celsius|°c|degree"],
            "correct_answer": "Beeswax melts at 62-65 degrees C (335-338 K). Precise range depends on composition. Pure beeswax: 62-64 C, capping wax may be slightly higher at 63-65 C.",
            "agent": "tutkija",
            "difficulty": "easy",
            "category": "material_science",
        },
        {
            "q": "Explain the waggle dance communication system. How does a forager bee encode distance and direction to a food source?",
            "expected_keywords": ["waggle", "distance", "direction", "sun", "angle", "duration", "gravity"],
            "correct_answer": "The waggle dance encodes food source location: direction is indicated by the angle of the waggle run relative to vertical (representing the sun's azimuth), while distance is encoded by the duration of the waggle phase (approximately 1 second per kilometer). Figure-eight pattern: waggle run + return circuits.",
            "agent": "tutkija",
            "difficulty": "medium",
            "category": "bee_science",
        },
        {
            "q": "What is the scientific mechanism behind varroa's harm to bees? It's not just blood-sucking, is it?",
            "expected_keywords": ["virus", "DWV", "fat body|fat-body|fat", "body|tissue|reserve", "immune", "vector", "deformed", "wing"],
            "correct_answer": "Varroa destructor feeds on fat body tissue (not hemolymph as once thought), which cripples the bee's immune system and reduces vitellogenin production. Critically, varroa is a vector for viruses, especially Deformed Wing Virus (DWV). The mite activates and transmits DWV during feeding, causing wing deformity, shortened lifespan, and colony collapse.",
            "agent": "tutkija",
            "difficulty": "hard",
            "category": "bee_science",
        },
        {
            "q": "A study claims 'neonicotinoids at 10 ppb cause 50% colony loss.' What methodological concerns should a researcher raise?",
            "expected_keywords": ["dose", "field", "lab", "confound|bias|variable", "control", "sample", "expos", "chronic"],
            "correct_answer": "Key concerns: 1) Lab vs field relevance -actual field exposure may differ. 2) Chronic vs acute exposure duration. 3) Confounding factors (varroa, nutrition, other pesticides). 4) Sample size and replication. 5) Colony loss definition (complete death vs weakening). 6) Environmental dose realism -10 ppb field-relevant?",
            "agent": "tutkija",
            "difficulty": "expert",
            "category": "research_methodology",
        },
        {
            "q": "How do bees maintain the brood nest at exactly 34.5-35.5 degrees C? What mechanisms are involved?",
            "expected_keywords": ["fan", "cluster", "water", "evaporat", "muscle", "shiver|shivering|vibrat", "thermoregulat|regulat"],
            "correct_answer": "Bees use active thermoregulation: Cooling -fanning wings (convection), spreading water droplets (evaporative cooling), reducing cluster density. Heating -flight muscle shivering (endothermic), clustering tightly. Individual heater bees press against capped cells. The system maintains plus/minus 0.5 C precision across thousands of cells.",
            "agent": "tutkija",
            "difficulty": "medium",
            "category": "bee_science",
        },
        {
            "q": "Compare the effectiveness of oxalic acid vs formic acid for varroa treatment. When would you choose each?",
            "expected_keywords": ["oxalic", "formic", "brood", "broodless", "efficacy|effectiveness|effective", "penetrat", "season|timing|time"],
            "correct_answer": "Oxalic acid: 95%+ efficacy on phoretic mites, does NOT penetrate brood cells, ideal during broodless period (winter). Formic acid: 60-80% efficacy, DOES penetrate capped brood cells (kills mites in cells), used during brood season (summer/fall). Choose oxalic for winter treatment, formic for in-season treatment when brood is present.",
            "agent": "tutkija",
            "difficulty": "hard",
            "category": "treatment_science",
        },
    ],
}

# ══════════════════════════════════════════════════════════════════
# CROSS-AGENT TESTS -6 multi-agent scenarios
# ══════════════════════════════════════════════════════════════════

CROSS_AGENT_TESTS = [
    {
        "q": "It's October. Nighttime temperatures drop to -5 C. Cottage is empty, beehives are at the cottage. The beekeeper needs to winterize both property and colonies. What's the priority list?",
        "expected_keywords": ["pipe", "drain|seal|close", "water|moisture", "varroa", "treat", "stores|reserve|food", "feed|syrup|supplement", "insulate|wrap|protect", "heat"],
        "correct_answer": "Priority 1 (immediate): Drain cottage water pipes -freeze damage is irreversible. Priority 2: Check bee colony stores (need 15-20 kg). Priority 3: Apply final varroa treatment (oxalic acid during broodless period). Priority 4: Insulate/wrap hives. Priority 5: Secure cottage (shutters, anti-rodent). Priority 6: Set up remote temperature monitoring for both cottage and hives.",
        "agent": "cross_agent",
        "difficulty": "hard",
        "category": "multi_domain",
    },
    {
        "q": "Factory electricity bill jumped 40%. The production manager suspects HVAC but the beekeeper's honey extraction room is also in the building with a 5kW heater running 24/7. Diagnose the cost spike.",
        "expected_keywords": ["heater", "5kW|5 kw|5 kilowatt", "hvac", "24", "kWh", "cost", "thermostat", "extract|honey room|beekeep"],
        "correct_answer": "5kW heater running 24/7 = 120 kWh/day = 3600 kWh/month. At 0.12 EUR/kWh = 432 EUR/month just for the extraction room heater. This alone could explain a 40% jump. Check: is the thermostat working? Does it need to run 24/7? HVAC should also be audited but the honey room heater is the obvious suspect.",
        "agent": "cross_agent",
        "difficulty": "medium",
        "category": "energy_investigation",
    },
    {
        "q": "A beekeeper reports dead bees near the hives and brown residue on the landing board. The weather was hot (32 C) yesterday and a neighboring farm sprayed crops. What domains should investigate?",
        "expected_keywords": ["pesticide", "poison|poisoning|toxic", "spray", "sample", "dead", "report", "authorit", "weather"],
        "correct_answer": "Multi-domain issue: 1) tautivahti: collect dead bee samples, check for pesticide poisoning symptoms (tongue extended, twitching). 2) meteorologi: hot weather + spraying = bees actively foraging during spray. 3) tutkija: send samples for pesticide residue analysis. 4) tarhaaja: close hive entrances temporarily if spraying continues. Report to authorities as potential poisoning incident.",
        "agent": "cross_agent",
        "difficulty": "hard",
        "category": "incident_response",
    },
    {
        "q": "Smart home detects: humidity 75% in cottage basement, temperature 12 C, electricity price spiking to 0.50 EUR/kWh. The dehumidifier uses 500W. Run it now or wait?",
        "expected_keywords": ["mold", "run|operat|use|turn on", "humidity", "dehumidif", "risk", "price", "cost", "health"],
        "correct_answer": "Run the dehumidifier despite high electricity cost. 75% humidity at 12 C = active mold risk. Mold remediation costs hundreds/thousands vs dehumidifier cost: 0.5kW * 0.50 EUR = 0.25 EUR/hour. Health and structural damage from mold far outweigh electricity savings. Can reduce runtime once humidity drops below 60%.",
        "agent": "cross_agent",
        "difficulty": "medium",
        "category": "cost_vs_risk",
    },
    {
        "q": "Late June: 3 days of 28 C forecast, then thunderstorm with 25 m/s gusts. Bee yard has 20 hives on stands, some top-heavy with 3 honey supers. Action plan?",
        "expected_keywords": ["strap", "secure", "harvest", "wind", "super", "weight", "stand", "tip"],
        "correct_answer": "Before storm: 1) Strap/weight all hives (top-heavy with 3 supers = tip risk in 25 m/s gusts). 2) Consider emergency harvest if supers are full -reduces weight and saves honey from potential damage. 3) Ensure stands are level and anchored. 4) Secure lids with bricks/straps. After storm: inspect for tipped hives, check for branch damage.",
        "agent": "cross_agent",
        "difficulty": "hard",
        "category": "weather_beekeeping",
    },
    {
        "q": "A remote monitoring system shows: cottage temp 5 C (set 8 C), electricity consumption zero, and the nearest weather station reports -20 C with power outages in the area. Create a response plan.",
        "expected_keywords": ["power", "outage", "pipe", "freez", "neighbor", "emergency", "drain", "generator"],
        "correct_answer": "Emergency response: 1) Confirm power outage (zero consumption + sub-set-point temp). 2) Contact local neighbor/caretaker for physical check. 3) Contact utility for estimated restoration time. 4) If restoration >6 hours: dispatch someone to drain water system (pipes will freeze at these temps). 5) If available: portable generator for minimum heating. Cottage will reach 0 C within hours at -20 C without heat.",
        "agent": "cross_agent",
        "difficulty": "expert",
        "category": "emergency_response",
    },
]

# ══════════════════════════════════════════════════════════════════
# HALLUCINATION TRAPS -6 trick questions
# ══════════════════════════════════════════════════════════════════

HALLUCINATION_TRAPS = [
    {
        "q": "What is the standard dosage of amoxicillin for treating American Foulbrood in Finland?",
        "expected_keywords": ["not", "no", "antibiotic", "banned", "illegal", "burn", "prohibited", "EU"],
        "correct_answer": "Trick question. Antibiotics for bee diseases are BANNED in the EU/Finland. American Foulbrood treatment in Finland requires destruction (burning) of infected colonies and equipment. There is no antibiotic treatment protocol.",
        "agent": "hallucination_trap",
        "difficulty": "hard",
        "category": "false_premise",
    },
    {
        "q": "How many legs does a bee have? Answer with a number only.",
        "expected_keywords": ["6", "six"],
        "correct_answer": "6. All insects have 6 legs. This is a baseline factual question to check for obvious errors.",
        "agent": "hallucination_trap",
        "difficulty": "easy",
        "category": "basic_fact",
    },
    {
        "q": "What color is bee blood?",
        "expected_keywords": ["clear|transluc", "yellow|gold", "hemolymph", "not red|no red", "pale|light", "colorless|no color"],
        "correct_answer": "Bees have hemolymph, not blood. It is clear to pale yellow, not red, because it does not contain hemoglobin or red blood cells. Hemolymph does not carry oxygen (that's done via tracheal system).",
        "agent": "hallucination_trap",
        "difficulty": "medium",
        "category": "tricky_fact",
    },
    {
        "q": "Can you use a standard household microwave to melt crystallized honey for extraction?",
        "expected_keywords": ["no", "uneven", "overheat|burn|scorch", "destroy", "enzyme", "HMF", "warm", "water bath|water-bath|warm water|bain|double boiler"],
        "correct_answer": "Not recommended. Microwaves heat unevenly, creating hot spots that destroy enzymes and increase HMF (hydroxymethylfurfural) levels, degrading honey quality. Proper method: warm water bath at max 40 C for gentle, even decrystallization.",
        "agent": "hallucination_trap",
        "difficulty": "medium",
        "category": "bad_practice",
    },
    {
        "q": "What is the maximum safe CO level for continuous human exposure in a home, in ppm?",
        "expected_keywords": ["25", "ppm", "TWA|time-weighted|time weighted", "safe|health|risk|limit"],
        "correct_answer": "25 ppm (8-hour TWA per occupational standards). Short-term (15-minute) limit is 50 ppm. Above 50 ppm, evacuate. CO detectors typically alarm at 50 ppm.",
        "agent": "hallucination_trap",
        "difficulty": "medium",
        "category": "safety_fact",
    },
    {
        "q": "A beekeeper claims that painting hives with used motor oil protects the wood and is a traditional Finnish method. Is this safe?",
        "expected_keywords": ["no", "toxic", "contamin", "poison", "chemical", "unsafe", "not safe", "harmful"],
        "correct_answer": "Absolutely not safe. Used motor oil contains heavy metals, PAHs, and toxic compounds that contaminate beeswax and honey. It is also an environmental pollutant. Traditional wood protection uses linseed oil or specialized hive paint. Using motor oil is illegal under environmental regulations.",
        "agent": "hallucination_trap",
        "difficulty": "hard",
        "category": "bad_practice",
    },
]


# ══════════════════════════════════════════════════════════════════
# Combined test list
# ══════════════════════════════════════════════════════════════════

def build_all_tests(agent_filter=None, difficulty_filter=None):
    """Build filtered test list."""
    tests = []

    # Agent-specific tests
    for agent_name, agent_tests in AGENT_TESTS.items():
        if agent_filter and agent_filter != agent_name:
            continue
        tests.extend(agent_tests)

    # Cross-agent tests
    if not agent_filter or agent_filter == "cross_agent":
        tests.extend(CROSS_AGENT_TESTS)

    # Hallucination traps
    if not agent_filter or agent_filter == "hallucination_trap":
        tests.extend(HALLUCINATION_TRAPS)

    # Difficulty filter
    if difficulty_filter:
        difficulty_order = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}
        min_level = difficulty_order.get(difficulty_filter, 0)
        tests = [t for t in tests if difficulty_order.get(t["difficulty"], 0) >= min_level]

    return tests


ALL_TESTS = build_all_tests()


# ══════════════════════════════════════════════════════════════════
# Benchmark runner
# ══════════════════════════════════════════════════════════════════

def run_agent_benchmark(tests, model, agent_name=None):
    """Run benchmark for a set of tests. Returns list of result dicts."""
    results = []
    label = agent_name or "all"
    print(f"\n  Running {len(tests)} tests for [{label}] with model {model}")
    print(f"  {'#':<4s} {'Difficulty':<10s} {'Score':>7s} {'Time':>7s} {'Question':<50s}")
    print(f"  {'-'*82}")

    for i, test in enumerate(tests, 1):
        agent_key = test.get("agent", "tarhaaja")
        system_prompt = AGENT_PERSONAS.get(agent_key, "")
        prompt = f"Answer concisely and factually in English. Include specific numbers, thresholds, and measurements where relevant.\n\n{test['q']}"
        resp = ollama_generate(model, prompt, system=system_prompt, num_predict=500)

        # Strip thinking tags before scoring
        cleaned = strip_thinking(resp["text"])
        ev = evaluate(cleaned, test["expected_keywords"])

        status = "PASS" if ev["pass"] else "FAIL"
        q_short = test["q"][:48]
        print(f"  {i:<4d} {test['difficulty']:<10s} {ev['hits']:>7s} {resp['time_s']:>6.1f}s {q_short}")

        results.append({
            "question": test["q"],
            "agent": test.get("agent", "unknown"),
            "difficulty": test["difficulty"],
            "category": test.get("category", ""),
            "expected_keywords": test["expected_keywords"],
            "correct_answer": test.get("correct_answer", ""),
            "response": cleaned,
            "raw_response": resp["text"],
            "score": ev["score"],
            "hits": ev["hits"],
            "passed": ev["pass"],
            "time_s": resp["time_s"],
            "tokens": resp["tokens"],
            "tok_s": resp["tok_s"],
            "error": resp["error"],
        })

    return results


def run_full_benchmark(model, agent_filter=None, difficulty_filter=None):
    """Run the complete benchmark suite."""
    tests = build_all_tests(agent_filter, difficulty_filter)

    if not tests:
        print("  No tests match the given filters.")
        return []

    if agent_filter:
        return run_agent_benchmark(tests, model, agent_filter)

    # Run grouped by agent for clearer output
    all_results = []
    agent_groups = defaultdict(list)
    for t in tests:
        agent_groups[t["agent"]].append(t)

    for agent_name in agent_groups:
        group_tests = agent_groups[agent_name]
        results = run_agent_benchmark(group_tests, model, agent_name)
        all_results.extend(results)

    return all_results


# ══════════════════════════════════════════════════════════════════
# Summary reporting
# ══════════════════════════════════════════════════════════════════

def print_summary(results):
    """Print results breakdown by agent and difficulty."""
    if not results:
        return

    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")

    # By agent
    agent_stats = defaultdict(lambda: {"total": 0, "passed": 0, "score_sum": 0.0, "time_sum": 0.0})
    for r in results:
        a = r["agent"]
        agent_stats[a]["total"] += 1
        agent_stats[a]["passed"] += 1 if r["passed"] else 0
        agent_stats[a]["score_sum"] += r["score"]
        agent_stats[a]["time_sum"] += r["time_s"]

    print(f"\n  {'Agent':<20s} {'Tests':>6s} {'Passed':>8s} {'Rate':>7s} {'Avg Score':>10s} {'Avg Time':>10s}")
    print(f"  {'-'*65}")
    for agent_name in sorted(agent_stats.keys()):
        s = agent_stats[agent_name]
        n = s["total"]
        rate = s["passed"] / n if n else 0
        avg_score = s["score_sum"] / n if n else 0
        avg_time = s["time_sum"] / n if n else 0
        print(f"  {agent_name:<20s} {n:>6d} {s['passed']:>8d} {rate:>6.0%} {avg_score:>10.2f} {avg_time:>9.1f}s")

    # By difficulty
    diff_stats = defaultdict(lambda: {"total": 0, "passed": 0, "score_sum": 0.0})
    for r in results:
        d = r["difficulty"]
        diff_stats[d]["total"] += 1
        diff_stats[d]["passed"] += 1 if r["passed"] else 0
        diff_stats[d]["score_sum"] += r["score"]

    print(f"\n  {'Difficulty':<12s} {'Tests':>6s} {'Passed':>8s} {'Rate':>7s} {'Avg Score':>10s}")
    print(f"  {'-'*47}")
    for diff in ["easy", "medium", "hard", "expert"]:
        if diff not in diff_stats:
            continue
        s = diff_stats[diff]
        n = s["total"]
        rate = s["passed"] / n if n else 0
        avg_score = s["score_sum"] / n if n else 0
        print(f"  {diff:<12s} {n:>6d} {s['passed']:>8d} {rate:>6.0%} {avg_score:>10.2f}")

    # Overall
    total = len(results)
    total_passed = sum(1 for r in results if r["passed"])
    total_score = sum(r["score"] for r in results)
    total_time = sum(r["time_s"] for r in results)
    print(f"\n  {'OVERALL':<12s} {total:>6d} {total_passed:>8d} {total_passed/total:>6.0%} {total_score/total:>10.2f}")
    print(f"  Total time: {total_time:.1f}s | Avg: {total_time/total:.1f}s per question")

    # Worst performers (failed questions)
    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n  FAILED QUESTIONS ({len(failed)}):")
        print(f"  {'-'*65}")
        for r in failed:
            print(f"  [{r['agent']}/{r['difficulty']}] {r['question'][:60]}")
            print(f"    Score: {r['hits']} | Expected: {', '.join(r['expected_keywords'][:5])}")


# ══════════════════════════════════════════════════════════════════
# Dry run
# ══════════════════════════════════════════════════════════════════

def print_dry_run(agent_filter=None, difficulty_filter=None):
    """Print all questions without running them."""
    tests = build_all_tests(agent_filter, difficulty_filter)

    print(f"\n{'='*70}")
    print(f"  DRY RUN - {len(tests)} questions")
    print(f"{'='*70}")

    current_agent = None
    for i, t in enumerate(tests, 1):
        if t["agent"] != current_agent:
            current_agent = t["agent"]
            print(f"\n  --- {current_agent.upper()} ---")
        diff_tag = f"[{t['difficulty']}]"
        print(f"  {i:>3d}. {diff_tag:<10s} {t['q'][:80]}")
        print(f"       Keywords: {', '.join(t['expected_keywords'][:5])}")

    print(f"\n  Total: {len(tests)} questions")

    # Count by difficulty
    from collections import Counter
    diff_counts = Counter(t["difficulty"] for t in tests)
    print(f"  Breakdown: {', '.join(f'{d}: {c}' for d, c in sorted(diff_counts.items()))}")


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="WaggleDance Benchmark v3 -Agent Persona Testing")
    parser.add_argument("--agent", type=str, default=None,
                        help="Run tests for a single agent (e.g., tarhaaja, tautivahti, cross_agent, hallucination_trap)")
    parser.add_argument("--difficulty", type=str, default=None, choices=["easy", "medium", "hard", "expert"],
                        help="Minimum difficulty level (includes this level and above)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print questions without running Ollama")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Ollama model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  WaggleDance Benchmark v3 - Agent Persona & Difficulty Grading")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Model: {args.model} | Agent: {args.agent or 'ALL'} | Min difficulty: {args.difficulty or 'ALL'}")
    print(f"{'='*70}")

    # Dry run mode
    if args.dry_run:
        print_dry_run(args.agent, args.difficulty)
        return

    # Check Ollama is running
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"  Ollama running. Models available: {len(models)}")
        if args.model not in models and f"{args.model}:latest" not in models:
            print(f"  WARNING: Model '{args.model}' not found in Ollama. Available: {', '.join(models[:5])}")
    except Exception:
        print(f"  ERROR: Ollama not running at {OLLAMA_URL}!")
        sys.exit(1)

    # Run benchmark
    results = run_full_benchmark(args.model, args.agent, args.difficulty)

    # Print summary
    print_summary(results)

    # Save results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    outfile = f"benchmark_v3_{timestamp}.json"
    try:
        report = {
            "timestamp": datetime.now().isoformat(),
            "model": args.model,
            "agent_filter": args.agent,
            "difficulty_filter": args.difficulty,
            "total_tests": len(results),
            "total_passed": sum(1 for r in results if r["passed"]),
            "pass_rate": round(sum(1 for r in results if r["passed"]) / max(len(results), 1), 3),
            "avg_score": round(sum(r["score"] for r in results) / max(len(results), 1), 3),
            "total_time_s": round(sum(r["time_s"] for r in results), 1),
            "results": results,
        }
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  Saved to {outfile}")
    except Exception as e:
        print(f"\n  WARNING: Could not save results: {e}")

    print(f"\n{'='*70}")
    print(f"  Benchmark v3 complete!")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
