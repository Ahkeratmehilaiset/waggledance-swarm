#!/usr/bin/env python3
"""Fix 5: Replace placeholder eval_questions with real domain questions.

Replaces generic "Operatiivinen lisäkysymys #N?" and "Operatiivinen päätöskysymys #N?"
with real Finnish domain questions referencing actual YAML sections.
"""

import yaml
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
AGENTS_DIR = PROJECT_DIR / "agents"

# Real domain questions for each agent (replace the placeholder batch)
REPLACEMENT_QUESTIONS = {
    "baker": [
        {"q": "Mikä on leivän sisälämpötilan kypsyysraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.bread_core_temp_c.value", "source": "src:LEI1"},
        {"q": "Kuinka kauan leivinuuni vaatii esilämmitystä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.leivinuuni_heat_hours.value", "source": "src:LEI1"},
        {"q": "Mikä on taikinan kosteusprosentin ihannetaso?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.dough_hydration_pct.value", "source": "src:LEI1"},
        {"q": "Kuinka pitkään jauhoja voi varastoida?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.flour_storage_months.value", "source": "src:LEI1"},
        {"q": "Miten hapanjuuren kuolema tunnistetaan?", "a_ref": "FAILURE_MODES[0].detection", "source": "src:LEI1"},
        {"q": "Mitä tehdään kun leipä jää raa'aksi sisältä?", "a_ref": "FAILURE_MODES[1].action", "source": "src:LEI1"},
        {"q": "Mikä on uunin oikea lämpötila leivälle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.oven_temp_bread_c.value", "source": "src:LEI1"},
        {"q": "Mitä leivontaa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:LEI1"},
        {"q": "Mitä leivontaa tehdään kesällä?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:LEI1"},
        {"q": "Mikä on hapanjuuren aktiivisuuden mittari?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sourdough_activity.value", "source": "src:LEI1"},
    ],
    "cleaning_manager": [
        {"q": "Kuinka usein sauna pestään perusteellisesti?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sauna_wash_interval_weeks.value", "source": "src:SII1"},
        {"q": "Mikä on sisäilman CO2-pitoisuuden yläraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.indoor_air_quality_co2_ppm.value", "source": "src:SII1"},
        {"q": "Kuinka usein tehdään syväpuhdistus?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.deep_clean_interval_months.value", "source": "src:SII1"},
        {"q": "Miten homeenlöytö käsitellään?", "a_ref": "FAILURE_MODES[0].action", "source": "src:SII1"},
        {"q": "Miten viemärinhaju selvitetään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:SII1"},
        {"q": "Mikä on homeen tunnistusohje?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.mold_detection.value", "source": "src:SII1"},
        {"q": "Suositaanko ympäristöystävällisiä pesuaineita?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.cleaning_products_eco.value", "source": "src:SII1"},
        {"q": "Mitä siivoustehtäviä tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:SII1"},
        {"q": "Mitä siivoustehtäviä tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:SII1"},
        {"q": "Mitä siivoustehtäviä tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:SII1"},
    ],
    "disease_monitor": [
        {"q": "Miten kalkkisikiö tunnistetaan pesässä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.chalkbrood_frame_pct.value", "source": "src:TAU1"},
        {"q": "Mitkä ovat DWV:n merkit?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.deformed_wing_virus_signs.value", "source": "src:TAU1"},
        {"q": "Mikä on EFB:n tunnistuskynnys?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.efb_threshold.value", "source": "src:TAU1"},
        {"q": "Mitä tehdään massakuolemaepäilyssä?", "a_ref": "FAILURE_MODES[1].action", "source": "src:TAU1"},
        {"q": "Mikä on noseman itiömäärän hoitoraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.nosema_spore_count.value", "source": "src:TAU1"},
        {"q": "Mitä tautiseurantaa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:TAU1"},
        {"q": "Mitä tautiseurantaa tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:TAU1"},
        {"q": "Kenelle AFB:stä ilmoitetaan lakisääteisesti?", "a_ref": "COMPLIANCE_AND_LEGAL.afb", "source": "src:TAU1"},
        {"q": "Mitä lääkinnällisiä valmisteita saa käyttää?", "a_ref": "COMPLIANCE_AND_LEGAL.veterinary", "source": "src:TAU1"},
        {"q": "Mitkä ovat kalkkisikiön raja-arvot kehyskohtaisesti?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.chalkbrood_frame_pct.action", "source": "src:TAU1"},
    ],
    "electrician": [
        {"q": "Mikä on vikavirtasuojan suurin sallittu laukaisuvirta?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.rcd_trip_current_max_ma.value", "source": "src:SAH1"},
        {"q": "Mikä on pistorasiaryhmän nimellisvirran maksimi?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.socket_group_rated_current_max_a.value", "source": "src:SAH1"},
        {"q": "Miten toistuva RCD-laukeaminen selvitetään?", "a_ref": "FAILURE_MODES[0].action", "source": "src:SAH1"},
        {"q": "Miten lämpenevä liitos tunnistetaan?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:SAH1"},
        {"q": "Mikä on ulkojatkojohdon suojaluokka?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.outdoor_extension_cable_rating.value", "source": "src:SAH1"},
        {"q": "Onko ylijännitesuojaus asennettu?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.surge_protection_presence.value", "source": "src:SAH1"},
        {"q": "Mikä on pääsulakkeen mitoitus?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.main_fuse_rating_a.value", "source": "src:SAH1"},
        {"q": "Mitä sähkötöitä tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:SAH1"},
        {"q": "Mitä sähkötöitä tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:SAH1"},
        {"q": "Mitä sähkötöitä tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:SAH1"},
    ],
    "entertainment_chief": [
        {"q": "Mikä on ruutuajan suositeltu enimmäismäärä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.screen_time_max_h.value", "source": "src:VII1"},
        {"q": "Mikä on PS5:n tuuletuksen kriittinen lämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.ps5_ventilation_temp_c.value", "source": "src:VII1"},
        {"q": "Mikä on optimaalinen pelaajasmäärä peli-illassa?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.game_night_players_optimal.value", "source": "src:VII1"},
        {"q": "Tarvitseeko PS5 UPS-varavirtalähteen?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.ups_for_ps5.value", "source": "src:VII1"},
        {"q": "Mitä tehdään kun PS5 ylikuumenee?", "a_ref": "FAILURE_MODES[0].action", "source": "src:VII1"},
        {"q": "Miten sähkökatkos pelisession aikana hoidetaan?", "a_ref": "FAILURE_MODES[1].action", "source": "src:VII1"},
        {"q": "Mitä pelejä suositaan perinteisesti?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.traditional_games.value", "source": "src:VII1"},
        {"q": "Mitä viihdettä suositaan keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:VII1"},
        {"q": "Mitä viihdettä suositaan kesällä?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:VII1"},
        {"q": "Mitä viihdettä suositaan talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:VII1"},
    ],
    "hive_security": [
        {"q": "Mikä on sähköaidan minimijännite?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.electric_fence_voltage_kv.value", "source": "src:TUR1"},
        {"q": "Mikä on aidan maadoituksen vastusraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fence_ground_resistance_ohm.value", "source": "src:TUR1"},
        {"q": "Kuinka kaukaa karhu voi vahingoittaa tarhan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.bear_damage_radius_km.value", "source": "src:TUR1"},
        {"q": "Mikä on aidan vähimmäiskorkeus?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fence_height_cm.value", "source": "src:TUR1"},
        {"q": "Kuinka pitkäksi ruoho saa kasvaa aidan alla?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.grass_under_fence_max_cm.value", "source": "src:TUR1"},
        {"q": "Mitä tehdään kun karhu on murtautunut aitaan?", "a_ref": "FAILURE_MODES[0].action", "source": "src:TUR1"},
        {"q": "Miten aidan jännite tarkistetaan?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:TUR1"},
        {"q": "Mitä turvallisuustoimia tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:TUR1"},
        {"q": "Mitä turvallisuustoimia tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:TUR1"},
        {"q": "Mitä turvallisuustoimia tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:TUR1"},
    ],
    "hive_temperature": [
        {"q": "Mikä on sikiöpesän ihannelämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.brood_nest_temp_c.value", "source": "src:LAM1"},
        {"q": "Mikä on talvipallon ytimen lämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.winter_cluster_core_c.value", "source": "src:LAM1"},
        {"q": "Mikä on pesän kosteuden yläraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.hive_humidity_rh_pct.value", "source": "src:LAM1"},
        {"q": "Paljonko pesä saa menettää painoa viikossa talvella?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.weight_loss_winter_kg_per_week.value", "source": "src:LAM1"},
        {"q": "Mikä on äkillisen painonpudotuksen hälytysraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sudden_weight_drop_kg.value", "source": "src:LAM1"},
        {"q": "Milloin kevään painonnousu alkaa normaalisti?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.spring_weight_increase_start.value", "source": "src:LAM1"},
        {"q": "Mitä tehdään kun anturi ei lähetä dataa?", "a_ref": "FAILURE_MODES[0].action", "source": "src:LAM1"},
        {"q": "Miten äkillinen lämpötilan putoaminen käsitellään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:LAM1"},
        {"q": "Mitä lämpötilaseurantaa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:LAM1"},
        {"q": "Mitä lämpötilaseurantaa tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LAM1"},
    ],
    "hvac_specialist": [
        {"q": "Missä lämpötilassa putket ovat jäätymisriskissä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.pipe_freeze_risk_temp_c.value", "source": "src:LVI1"},
        {"q": "Mikä on sisäilman kosteuden yläraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.indoor_humidity_high_rh.value", "source": "src:LVI1"},
        {"q": "Miten vesimittarin vuoto havaitaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.water_meter_leak_delta.value", "source": "src:LVI1"},
        {"q": "Kuinka usein viemärin vesilukko kuivuu?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sewer_trap_dry_risk_days.value", "source": "src:LVI1"},
        {"q": "Tarvitseeko lämminvesivaraaja tyhjennystä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.boiler_drain_required.value", "source": "src:LVI1"},
        {"q": "Mitä tehdään putkien jäätyessä?", "a_ref": "FAILURE_MODES[0].action", "source": "src:LVI1"},
        {"q": "Miten hidas vuoto tunnistetaan?", "a_ref": "FAILURE_MODES[1].detection", "source": "src:LVI1"},
        {"q": "Mitä LVI-töitä tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:LVI1"},
        {"q": "Mitä LVI-töitä tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:LVI1"},
        {"q": "Mitä LVI-töitä tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LVI1"},
    ],
    "inventory_chief": [
        {"q": "Mikä on sokerin tilauspiste kilogrammoina?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.reorder_point_sugar_kg.value", "source": "src:INV1"},
        {"q": "Kuinka monen päivän polttoainereservi pidetään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fuel_reserve_days.value", "source": "src:INV1"},
        {"q": "Kuinka usein työkalut tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.tool_condition_check_months.value", "source": "src:INV1"},
        {"q": "Kuinka usein elintarvikkeiden päiväykset tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.food_expiry_check_weeks.value", "source": "src:INV1"},
        {"q": "Kuinka usein tehdään kattava inventaario?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.inventory_full_audit_months.value", "source": "src:INV1"},
        {"q": "Mitä tehdään kun sokeri loppuu ruokintakaudella?", "a_ref": "FAILURE_MODES[0].action", "source": "src:INV1"},
        {"q": "Miten rikkoutunut työkalu korvataan?", "a_ref": "FAILURE_MODES[1].action", "source": "src:INV1"},
        {"q": "Mitä inventaariotoimia tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:INV1"},
        {"q": "Mitä inventaariotoimia tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:INV1"},
        {"q": "Mitä inventaariotoimia tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:INV1"},
    ],
    "logistics": [
        {"q": "Mikä on sähköauton toimintamatka talvella?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.range_km_winter.value", "source": "src:LOG1"},
        {"q": "Miten lataussuunnitelma tehdään pitkälle matkalle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.charging_plan.value", "source": "src:LOG1"},
        {"q": "Kuinka pitkä matka on Korvenrannasta Helsinkiin?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.korvenranta_helsinki_km.value", "source": "src:LOG1"},
        {"q": "Kuinka pitkä matka on Korvenrannasta Kouvolaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.korvenranta_kouvola_km.value", "source": "src:LOG1"},
        {"q": "Mikä on hunajan kuljetuslämpötilan raja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.honey_transport_temp_c.value", "source": "src:LOG1"},
        {"q": "Mikä on kuormakapasiteetti kilogrammoina?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.load_capacity_kg.value", "source": "src:LOG1"},
        {"q": "Mitä tehdään kun akku loppuu kesken matkan?", "a_ref": "FAILURE_MODES[0].action", "source": "src:LOG1"},
        {"q": "Miten kelirikko-ongelma ratkaistaan?", "a_ref": "FAILURE_MODES[1].action", "source": "src:LOG1"},
        {"q": "Mitä logistiikkatoimia tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:LOG1"},
        {"q": "Mitä logistiikkatoimia tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:LOG1"},
    ],
    "math_physicist": [
        {"q": "Mikä on astepäivälaskelman kaava?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.deg_day_formula.value", "source": "src:MAT1"},
        {"q": "Mikä on tuulenpurevuuden laskukaava?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.wind_chill_formula.value", "source": "src:MAT1"},
        {"q": "Mikä on lämpöhäviön U-arvo?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.heat_loss_u_value.value", "source": "src:MAT1"},
        {"q": "Mikä on auringon kulman laskukaava?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.solar_angle_formula.value", "source": "src:MAT1"},
        {"q": "Mikä on tilastollisen luottamustason vaatimus?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.statistical_confidence.value", "source": "src:MAT1"},
        {"q": "Mitkä ovat optimoinnin rajoitteet?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.optimization_constraints.value", "source": "src:MAT1"},
        {"q": "Miten epärealistinen mallitulos tunnistetaan?", "a_ref": "FAILURE_MODES[0].detection", "source": "src:MAT1"},
        {"q": "Mitä tehdään kun datapisteitä on liian vähän?", "a_ref": "FAILURE_MODES[1].action", "source": "src:MAT1"},
        {"q": "Mitä laskentoja tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:MAT1"},
        {"q": "Mitä laskentoja tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:MAT1"},
    ],
    "movie_expert": [
        {"q": "Mikä on yleisöarvosanan vähimmäisraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.audience_rating_min.value", "source": "src:ELO1"},
        {"q": "Mikä on elokuvan enimmäiskesto minuuteissa?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.runtime_max_min.value", "source": "src:ELO1"},
        {"q": "Miten ikärajat huomioidaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.content_rating.value", "source": "src:ELO1"},
        {"q": "Miten genren ja tunnelman yhteys toimii?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.genre_mood_mapping.value", "source": "src:ELO1"},
        {"q": "Miten streaming-saatavuus tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.streaming_availability.value", "source": "src:ELO1"},
        {"q": "Mitä tehdään kun elokuva ei ole saatavilla?", "a_ref": "FAILURE_MODES[0].action", "source": "src:ELO1"},
        {"q": "Miten ikärajaylitys käsitellään lasten kanssa?", "a_ref": "FAILURE_MODES[1].action", "source": "src:ELO1"},
        {"q": "Mitä elokuvia suositaan keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:ELO1"},
        {"q": "Mitä elokuvia suositaan kesällä?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:ELO1"},
        {"q": "Mitä elokuvia suositaan talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:ELO1"},
    ],
    "nectar_scout": [
        {"q": "Mikä osoittaa mesikauden alkamisen?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.nectar_flow_start_indicator.value", "source": "src:NEK1"},
        {"q": "Paljonko painoa voi kertyä päivässä huippukaudella?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.peak_flow_rate_kg_per_day.value", "source": "src:NEK1"},
        {"q": "Mikä on nektarivirran loppumisen merkki?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.flow_end_indicator.value", "source": "src:NEK1"},
        {"q": "Milloin lisätään korotuskehä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.super_addition_trigger.value", "source": "src:NEK1"},
        {"q": "Mikä on hunajan kosteusraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.moisture_content_pct.value", "source": "src:NEK1"},
        {"q": "Mitkä ovat nektarin erittymisen edellytykset?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.nectar_secretion_conditions.value", "source": "src:NEK1"},
        {"q": "Mitä tehdään kun satokausi jää lyhyeksi?", "a_ref": "FAILURE_MODES[0].action", "source": "src:NEK1"},
        {"q": "Miten rypsin nopea kiteytyminen käsitellään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:NEK1"},
        {"q": "Mitä seurantaa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:NEK1"},
        {"q": "Mitä tehdään loppukesällä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:NEK1"},
    ],
    "nutritionist": [
        {"q": "Mikä on päivittäinen energiantarve kilokaloreina?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.daily_energy_kcal.value", "source": "src:RAV1"},
        {"q": "Paljonko proteiinia tarvitaan per painokilo?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.protein_g_per_kg.value", "source": "src:RAV1"},
        {"q": "Kuinka paljon nestettä päivässä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_day.value", "source": "src:RAV1"},
        {"q": "Mikä on D-vitamiinin suositusannos?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.vitamin_d_ug.value", "source": "src:RAV1"},
        {"q": "Kuinka usein kalaa viikossa omega-3:lle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.omega3_weekly_fish.value", "source": "src:RAV1"},
        {"q": "Mikä on lisätyn sokerin enimmäisosuus energiasta?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sugar_max_energy_pct.value", "source": "src:RAV1"},
        {"q": "Miten dehydraatio tunnistetaan ulkotyössä?", "a_ref": "FAILURE_MODES[0].detection", "source": "src:RAV1"},
        {"q": "Miten energiavaje korjataan pitkänä työpäivänä?", "a_ref": "FAILURE_MODES[1].action", "source": "src:RAV1"},
        {"q": "Mitä ravitsemuseroja on keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:RAV1"},
        {"q": "Mitä ravitsemuseroja on talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:RAV1"},
    ],
    "recycling": [
        {"q": "Mitkä ovat jätteen lajittelukategoriat?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.waste_sorting_categories.value", "source": "src:KIE1"},
        {"q": "Mikä on kompostin oikea lämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.compost_temp_c.value", "source": "src:KIE1"},
        {"q": "Miten vaarallinen jäte käsitellään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.hazardous_waste.value", "source": "src:KIE1"},
        {"q": "Kuinka usein jäteastia tyhjennetään?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.bin_pickup_interval_weeks.value", "source": "src:KIE1"},
        {"q": "Mikä on kierrätystavoite prosentteina?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.recycling_rate_target_pct.value", "source": "src:KIE1"},
        {"q": "Mitä tehdään kun komposti haisee?", "a_ref": "FAILURE_MODES[0].action", "source": "src:KIE1"},
        {"q": "Miten vaarallinen jäte sekajätteessä käsitellään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:KIE1"},
        {"q": "Mitä kierrätystehtäviä tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:KIE1"},
        {"q": "Mitä kierrätystehtäviä tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:KIE1"},
        {"q": "Mitä kierrätystehtäviä tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:KIE1"},
    ],
    "sauna_master": [
        {"q": "Mikä on saunan ihannelämpötila lauteilla?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.sauna_temp_c.value", "source": "src:SA1"},
        {"q": "Mikä on yhden saunomiskerran enimmäiskesto?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.session_max_min.value", "source": "src:SA1"},
        {"q": "Paljonko nestettä saunomiskerralla?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.hydration_l_per_session.value", "source": "src:SA1"},
        {"q": "Mikä tarkistetaan ennen saunan lämmittämistä?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.chimney_check_before_use.value", "source": "src:SA1"},
        {"q": "Mikä on paras jäähdytystapa?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.cool_down_method.value", "source": "src:SA1"},
        {"q": "Kuinka usein kiuaskivet tarkistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.kiuas_stone_check_years.value", "source": "src:SA1"},
        {"q": "Mitä tehdään kun löylyhuoneessa on savua?", "a_ref": "FAILURE_MODES[0].action", "source": "src:SA1"},
        {"q": "Miten pyörtyminen saunassa käsitellään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:SA1"},
        {"q": "Mitä saunahuoltoa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:SA1"},
        {"q": "Mitä saunahuoltoa tehdään talvella?", "a_ref": "SEASONAL_RULES[3].action", "source": "src:SA1"},
    ],
    "swarm_watcher": [
        {"q": "Milloin parveilukausi on Suomessa?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.swarm_season.value", "source": "src:PAR1"},
        {"q": "Kuinka usein pesä tarkistetaan parveiluaikana?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.inspection_interval_days.value", "source": "src:PAR1"},
        {"q": "Montako emokoppaa laukaisee toimenpiteen?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.queen_cell_count_trigger.value", "source": "src:PAR1"},
        {"q": "Miten yhdyskunnan ahtaus tunnistetaan?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.colony_overcrowding_indicator.value", "source": "src:PAR1"},
        {"q": "Millaisella säällä parveilriski on korkein?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.weather_swarm_risk.value", "source": "src:PAR1"},
        {"q": "Mitkä ovat merkit jo tapahtuneesta parveilusta?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.post_swarm_signs.value", "source": "src:PAR1"},
        {"q": "Mitä tehdään kun parvi on lähtenyt?", "a_ref": "FAILURE_MODES[0].action", "source": "src:PAR1"},
        {"q": "Miten emoton pesä hoidetaan parveilun jälkeen?", "a_ref": "FAILURE_MODES[1].action", "source": "src:PAR1"},
        {"q": "Mitä tehdään keväällä parveiluehkäisyyn?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:PAR1"},
        {"q": "Mikä on huippukauden toimintaohje?", "a_ref": "SEASONAL_RULES[1].action", "source": "src:PAR1"},
    ],
    "wilderness_chef": [
        {"q": "Mikä on jääkaapin oikea lämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fridge_temp_c.value", "source": "src:ERA1"},
        {"q": "Mikä on lihan sisälämpötilan kypsyysraja?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.meat_core_temp_c.value", "source": "src:ERA1"},
        {"q": "Kuinka pitkään kala säilyy tuoreena?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fish_freshness_hours.value", "source": "src:ERA1"},
        {"q": "Mikä on sienitunnistuksen varmuustaso?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.mushroom_identification_confidence.value", "source": "src:ERA1"},
        {"q": "Mikä on paloturvaetäisyys nuotiolle?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.fire_safety_distance_m.value", "source": "src:ERA1"},
        {"q": "Mikä on savustuslämpötila?", "a_ref": "DECISION_METRICS_AND_THRESHOLDS.smoke_cooking_temp_c.value", "source": "src:ERA1"},
        {"q": "Miten ruokamyrkytysepäily käsitellään?", "a_ref": "FAILURE_MODES[0].action", "source": "src:ERA1"},
        {"q": "Miten myrkkysieniepäily käsitellään?", "a_ref": "FAILURE_MODES[1].action", "source": "src:ERA1"},
        {"q": "Mitä eräruokaa tehdään keväällä?", "a_ref": "SEASONAL_RULES[0].action", "source": "src:ERA1"},
        {"q": "Mitä eräruokaa tehdään syksyllä?", "a_ref": "SEASONAL_RULES[2].action", "source": "src:ERA1"},
    ],
}


def fix_agent(agent_id: str, replacements: list[dict]) -> bool:
    """Replace placeholder eval_questions in an agent's core.yaml."""
    core_path = AGENTS_DIR / agent_id / "core.yaml"
    if not core_path.exists():
        print(f"  SKIP: {agent_id} — file not found")
        return False

    with open(core_path, encoding="utf-8") as f:
        content = f.read()

    # Check for placeholder patterns
    placeholder_patterns = [
        "Operatiivinen lisäkysymys",
        "Operatiivinen päätöskysymys",
    ]

    has_placeholder = any(p in content for p in placeholder_patterns)
    if not has_placeholder:
        print(f"  SKIP: {agent_id} — no placeholders found")
        return False

    # Parse YAML
    data = yaml.safe_load(content)
    if not data or "eval_questions" not in data:
        print(f"  SKIP: {agent_id} — no eval_questions section")
        return False

    # Filter out placeholder questions, keep real ones
    real_questions = []
    removed = 0
    for q in data["eval_questions"]:
        q_text = q.get("q", "")
        if any(p in q_text for p in placeholder_patterns):
            removed += 1
        else:
            real_questions.append(q)

    # Add replacement questions
    real_questions.extend(replacements)
    data["eval_questions"] = real_questions

    # Write back
    with open(core_path, "w", encoding="utf-8", newline="\n") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False, width=120)

    print(f"  FIXED: {agent_id} — removed {removed} placeholders, added {len(replacements)} real questions")
    return True


fixed = 0
for agent_id, questions in REPLACEMENT_QUESTIONS.items():
    if fix_agent(agent_id, questions):
        fixed += 1

print(f"\nResults: {fixed} agents fixed")
