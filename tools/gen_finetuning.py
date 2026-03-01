#!/usr/bin/env python3
"""Generate finetuning JSONL from agent YAML files"""
import yaml, json, os

AGENT_ORDER = [
    'core_dispatcher','luontokuvaaja','ornitologi','riistanvartija',
    'hortonomi','metsanhoitaja','fenologi','pienelain_tuholais',
    'entomologi','tahtitieteilija','valo_varjo','tarhaaja',
    'lentosaa','parveiluvahti','pesalampo','nektari_informaatikko',
    'tautivahti','pesaturvallisuus','limnologi','kalastusopas',
    'kalantunnistaja','rantavahti','jaaasiantuntija','meteorologi',
    'myrskyvaroittaja','mikroilmasto','ilmanlaatu','routa_maapera',
    'sahkoasentaja','lvi_asiantuntija','timpuri','nuohooja',
    'valaistusmestari','paloesimies','laitehuoltaja','kybervahti',
    'lukkoseppa','pihavahti','privaattisuus','erakokki',
    'leipuri','ravintoterapeutti','saunamajuri','viihdepaallikko',
    'elokuva_asiantuntija','inventaariopaallikko','kierratys_jate',
    'siivousvastaava','logistikko','matemaatikko_fyysikko'
]

out_lines = []
for agent_dir in AGENT_ORDER:
    path = f'agents/{agent_dir}/core.yaml'
    if not os.path.exists(path):
        continue
    with open(path, encoding='utf-8') as f:
        core = yaml.safe_load(f)
    agent_name = core.get('header', {}).get('agent_name', agent_dir)
    yaml_str = yaml.dump(core, allow_unicode=True, default_flow_style=False, sort_keys=False)
    system = f'Olet {agent_name} -agentti OpenClaw v1.4 -järjestelmässä.\n\n{yaml_str}'
    for q in core.get('eval_questions', [])[:30]:
        entry = {
            'messages': [
                {'role': 'system', 'content': system[:4000]},
                {'role': 'user', 'content': q['q']},
                {'role': 'assistant', 'content': f'[Vastaus perustuu: {q["a_ref"]}] [{q.get("source", "")}]'}
            ],
            'agent_id': agent_dir,
            'source': q.get('source', '')
        }
        out_lines.append(json.dumps(entry, ensure_ascii=False))

outpath = 'output/finetuning_data.jsonl'
with open(outpath, 'w', encoding='utf-8') as f:
    f.write('\n'.join(out_lines))
print(f'  ✅ finetuning_data.jsonl: {len(out_lines)} entries ({os.path.getsize(outpath):,} bytes)')
