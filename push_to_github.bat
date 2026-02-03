@echo off
echo ========================================
echo PUSHING TO GITHUB - MAIN BRANCH
echo ========================================
echo.

echo === BACKEND (RAD_AI) ===
cd C:\Users\Abdullah.Khan\RAD_AI
git add .
git commit -m "feat: Restore three-tier FROM-TO detection system"
git push origin main
echo.
echo Latest commit:
git log -1 --format="%%h - %%s (%%ar)"
echo.

echo === FRONTEND (airflow_frontend) ===
cd C:\Users\Abdullah.Khan\airflow_frontend
git add .
git commit -m "feat: Add FROM-TO columns in line list"
git push origin main
echo.
echo Latest commit:
git log -1 --format="%%h - %%s (%%ar)"
echo.

echo ========================================
echo DONE! Check GitHub now.
echo ========================================
pause
