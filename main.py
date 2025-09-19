"""
Dashboard application for Kalshi market analysis.
"""
import logging
import sys
import subprocess
import os

from config import Config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class DashboardApp:
    """Main application class for the dashboard."""
    
    def __init__(self):
        """Initialize the dashboard app."""
        self.config = Config()
    
    def run_dashboard(self):
        """Run the Streamlit dashboard."""
        
        logger.info("Starting Kalshi Market Analysis Dashboard...")
        
        # Set environment variables for Streamlit
        env = os.environ.copy()
        env['STREAMLIT_SERVER_PORT'] = str(self.config.DASHBOARD_PORT)
        env['STREAMLIT_SERVER_ADDRESS'] = self.config.DASHBOARD_HOST
        
        try:
            subprocess.run([
                sys.executable, '-m', 'streamlit', 'run', 'dashboard.py',
                '--server.port', str(self.config.DASHBOARD_PORT),
                '--server.address', self.config.DASHBOARD_HOST
            ], env=env)
        except KeyboardInterrupt:
            logger.info("Dashboard stopped")
        except Exception as e:
            logger.error(f"Error running dashboard: {e}")

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Kalshi Market Analysis Dashboard')
    parser.add_argument('--port', type=int, default=8501,
                       help='Dashboard port (default: 8501)')
    
    args = parser.parse_args()
    
    app = DashboardApp()
    app.config.DASHBOARD_PORT = args.port
    app.run_dashboard()

if __name__ == "__main__":
    main()
