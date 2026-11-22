"""Create private Docker settings, preserving existing AI and Docker choices."""
from pathlib import Path
from dotenv import dotenv_values, set_key

root = Path(__file__).resolve().parents[1]
destination = root / 'deploy' / 'local.env'
existing = dotenv_values(destination) if destination.exists() else {}
defaults = dotenv_values(root / 'deploy' / 'local.env.example')
legacy = dotenv_values(root / '.env')
settings = {**defaults, **{key: value for key, value in legacy.items() if key.startswith('AI_')}, **existing}
settings.setdefault('AI_MODEL_SELECTOR_ENABLED', '1')
for key, value in settings.items():
    if value is not None:
        set_key(str(destination), key, value, quote_mode='always')
print('Configuración local preparada; credenciales conservadas sin mostrarlas.')
