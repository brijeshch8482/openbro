# PowerShell wrapper — same as train.bat but for PS users.
#
# Usage:
#   .\train.ps1 scripts\resume_train.py
#   .\train.ps1 scripts\overnight_orchestrator.py
#   .\train.ps1 -m openbro.training.cli train run

$env:HF_HOME = "D:\caches\huggingface"
$env:HUGGINGFACE_HUB_CACHE = "D:\caches\huggingface\hub"
$env:TRANSFORMERS_CACHE = "D:\caches\huggingface"
$env:HF_DATASETS_CACHE = "D:\caches\huggingface\datasets"
$env:TORCH_HOME = "D:\caches\torch"
$env:PIP_CACHE_DIR = "D:\caches\pip"
$env:TMP = "D:\caches\temp"
$env:TEMP = "D:\caches\temp"
$env:OPENBRO_LLAMA_CPP_DIR = "D:\llama.cpp"
$env:PYTHONPATH = "D:\OpenBro;$env:PYTHONPATH"

& "D:\openbro-venv\Scripts\python.exe" @args
