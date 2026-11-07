"""The deployed bundle must contain the real public-card launcher."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_card_launcher_is_in_deployment_inputs():
    deploy = (ROOT / 'ops/windows/reboot/Deploy-WdRebootBundle.ps1').read_text(encoding='utf-8-sig')
    required = deploy.split('foreach ($required in @(', 1)[1].split('))', 1)[0]
    assert "'Invoke-WdQ4PublicCard.ps1'" in required
    assert (ROOT / 'ops/windows/reboot/Invoke-WdQ4PublicCard.ps1').is_file()
