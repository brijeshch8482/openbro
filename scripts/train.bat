@echo off
REM Wrapper that runs any training-pipeline script through the D-drive
REM venv, with all caches pointed at D so nothing ever touches C.
REM
REM Usage:
REM   train.bat resume_train.py
REM   train.bat overnight_orchestrator.py
REM   train.bat -m openbro.training.cli train run

setlocal

REM Force every ML cache to D.
set HF_HOME=D:\caches\huggingface
set HUGGINGFACE_HUB_CACHE=D:\caches\huggingface\hub
set TRANSFORMERS_CACHE=D:\caches\huggingface
set HF_DATASETS_CACHE=D:\caches\huggingface\datasets
set TORCH_HOME=D:\caches\torch
set PIP_CACHE_DIR=D:\caches\pip
set TMP=D:\caches\temp
set TEMP=D:\caches\temp
set OPENBRO_LLAMA_CPP_DIR=D:\llama.cpp

REM Ensure the OpenBro package itself is importable.
set PYTHONPATH=D:\OpenBro;%PYTHONPATH%

D:\openbro-venv\Scripts\python.exe %*
