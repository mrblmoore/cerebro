#!/bin/bash
# Setup script for Cerebrus MVP

set -e

echo "🤖 Setting up Cerebrus MVP..."

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python $python_version"

# Create virtual environment
if [ ! -d "backend/venv" ]; then
    echo "📦 Creating virtual environment..."
    cd backend
    python3 -m venv venv
    source venv/bin/activate
    cd ..
else
    echo "✓ Virtual environment exists"
    source backend/venv/bin/activate
fi

# Install dependencies
echo "📥 Installing dependencies..."
cd backend
pip install -q -r requirements.txt
cd ..

# Setup environment
if [ ! -f "backend/.env" ]; then
    echo "⚙️  Setting up environment..."
    cp backend/.env.example backend/.env
    echo "   - Edit backend/.env with your configuration"
    echo "   - Required: DATABASE_URL, OPENAI_API_KEY"
fi

# Create database tables
echo "🗄️  Initializing database..."
cd backend
python -c "from app.core.database import Base, engine; Base.metadata.create_all(bind=engine)"
cd ..

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit backend/.env with your configuration"
echo "2. Ensure PostgreSQL is running"
echo "3. Start Screenpipe from https://github.com/mediar-ai/screenpipe"
echo "4. Run: cd backend && source venv/bin/activate && uvicorn app.main:app --reload"
echo "5. Open http://localhost:8000/docs for API documentation"
