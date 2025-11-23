@echo off
echo Deploying to GitHub...
git add .
git commit -m "Update translations and site"
git push --force
echo Done!
pause
