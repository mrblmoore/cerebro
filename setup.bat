@echo off
REM Setup script for Cerebrus MVP (Windows)

setlocal enabledelayedexpansion

echo 🤖 Setting up Cerebrus MVP...

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python not found. Install Python 3.10+ first.
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set python_version=%%i
echo ✓ Python %python_version%

REM Create virtual environment
if not exist "backend\venv" (
    echo 📦 Creating virtual environment...
    cd backend
    python -m venv venv
    call venv\Scripts\activate.bat
    cd ..
) else (
    echo ✓ Virtual environment exists
    call backend\venv\Scripts\activate.bat
)

REM Install dependencies
echo 📥 Installing dependencies...
cd backend
pip install -q -r requirements.txt
cd ..

REM Setup environment
if not exist "backend\.env" (
    echo ⚙️  Setting up environment...
    copy backend\.env.example backend\.env
    echo    - Edit backend\.env with your configuration
    echo    - Required: DATABASE_URL, OPENAI_API_KEY
)

REM Create database tables
echo 🗄️  Initializing database...
cd backend
python -c "from app.core.database import Base, engine; Base.metadata.create_all(bind=engine)"
cd ..

echo.
echo ✅ Setup complete!
echo.
echo Next steps:
echo 1. Edit backend\.env with your configuration
echo 2. Ensure PostgreSQL is running
echo 3. Start Screenpipe from https://github.com/mediar-ai/screenpipe
echo 4. Run: cd backend ^&^& venv\Scripts\activate.bat ^&^& uvicorn app.main:app --reload
echo 5. Open http://localhost:8000/docs for API documentation
