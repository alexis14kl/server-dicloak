@echo off
cd /d C:\Users\NyGsoft\Desktop\server_dicloak
git add -A
git commit -m "server-dicloak-v1"
git remote add origin https://github.com/alexis14kl/server-dicloak.git
git branch -M main
git push -u origin main
del push.bat
