@echo off
setlocal
python "%~dp0download_models.py" --fl2va pruned_int8_convrot --ref2va pruned_int8_convrot --text-encoder nvfp4_awq
pause
