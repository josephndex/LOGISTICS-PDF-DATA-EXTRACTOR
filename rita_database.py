#!/usr/bin/env python3
"""
RITA Database Module
====================
Handles MySQL database operations for vehicle maintenance data.
Creates table if not exists, upserts data with duplicate detection.
"""

import os
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, List, Optional
import pandas as pd
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("rita_db")

# Table schema for maintenance data
MAINTENANCE_COLUMNS = {
    'INVOICE': 'VARCHAR(100)',
    'DATE': 'DATE',
    'VEHICLE': 'VARCHAR(50)',
    'DESCRIPTION': 'TEXT',
    'QUANTITY': 'INT',
    'UNIT_COST': 'DECIMAL(12,2)',
    'TOTAL': 'DECIMAL(12,2)',
    'SUPPLIER': 'VARCHAR(255)',
    'OWNER': 'VARCHAR(100)',
}

# Composite unique key
UNIQUE_KEY_COLUMNS = ['INVOICE', 'DESCRIPTION']


class RitaDatabaseManager:
    """Database manager for RITA maintenance data."""
    
    def __init__(self, env_path: Optional[str] = None):
        """Initialize database manager with credentials from .env file."""
        self.env_path = env_path or str(Path(__file__).parent / ".env")
        self.engine = None
        self.db_name = None
        self.host = None
        self.user = None
        self.password = None
        self._load_env()
    
    def _load_env(self):
        """Load database credentials from .env file."""
        if not os.path.exists(self.env_path):
            raise FileNotFoundError(f".env file not found: {self.env_path}")
        
        load_dotenv(dotenv_path=self.env_path)
        
        self.db_name = os.getenv("DB_NAME")
        self.host = os.getenv("DB_HOST")
        self.user = os.getenv("DB_USER")
        self.password = os.getenv("DB_PASSWORD")
        
        missing = []
        if not self.db_name:
            missing.append("DB_NAME")
        if not self.host:
            missing.append("DB_HOST")
        if not self.user:
            missing.append("DB_USER")
        if not self.password:
            missing.append("DB_PASSWORD")
        
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")
        
        logger.info(f"Loaded credentials for database: {self.db_name}@{self.host}")
    
    def _create_engine(self):
        """Create SQLAlchemy engine."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import QueuePool
        from urllib.parse import quote_plus
        
        # URL-encode password to handle special characters like @
        encoded_password = quote_plus(self.password)
        engine_url = f"mysql+mysqlconnector://{self.user}:{encoded_password}@{self.host}/{self.db_name}"
        
        self.engine = create_engine(
            engine_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=900,
            echo=False,
            connect_args={"connect_timeout": 10},
        )
        return self.engine
    
    def get_engine(self):
        """Get or create engine."""
        if self.engine is None:
            self._create_engine()
        return self.engine
    
    @contextmanager
    def get_connection(self):
        """Get database connection with automatic cleanup."""
        engine = self.get_engine()
        conn = engine.connect()
        try:
            yield conn
        finally:
            conn.close()
    
    def test_connection(self) -> bool:
        """Test database connection."""
        try:
            with self.get_connection() as conn:
                from sqlalchemy import text
                result = conn.execute(text("SELECT 1"))
                result.fetchone()
                logger.info("Database connection successful")
                return True
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False
    
    def table_exists(self, table_name: str) -> bool:
        """Check if table exists in database."""
        try:
            with self.get_connection() as conn:
                from sqlalchemy import text
                result = conn.execute(text(f"""
                    SELECT COUNT(*) FROM information_schema.tables 
                    WHERE table_schema = :db_name AND table_name = :table_name
                """), {"db_name": self.db_name, "table_name": table_name})
                count = result.fetchone()[0]
                return count > 0
        except Exception as e:
            logger.error(f"Error checking table existence: {e}")
            return False
    
    def create_maintenance_table(self, table_name: str = "maintainance") -> bool:
        """Create the maintenance table if it doesn't exist."""
        try:
            with self.get_connection() as conn:
                from sqlalchemy import text
                
                # Build column definitions
                col_defs = []
                for col_name, col_type in MAINTENANCE_COLUMNS.items():
                    col_defs.append(f"`{col_name}` {col_type}")
                
                # Add auto-increment ID and timestamps
                create_sql = f"""
                CREATE TABLE IF NOT EXISTS `{table_name}` (
                    `id` INT AUTO_INCREMENT PRIMARY KEY,
                    {', '.join(col_defs)},
                    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY `ux_invoice_desc` (`INVOICE`, `DESCRIPTION`(255))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
                
                conn.execute(text(create_sql))
                conn.commit()
                logger.info(f"Table '{table_name}' created or already exists")
                return True
                
        except Exception as e:
            logger.error(f"Error creating table: {e}")
            return False
    
    def get_existing_keys(self, table_name: str) -> set:
        """Get existing INVOICE|DESCRIPTION keys from database."""
        try:
            with self.get_connection() as conn:
                from sqlalchemy import text
                result = conn.execute(text(f"""
                    SELECT CONCAT(INVOICE, '|', DESCRIPTION) as dup_key 
                    FROM `{table_name}`
                """))
                keys = set(row[0] for row in result.fetchall())
                logger.info(f"Found {len(keys)} existing records in database")
                return keys
        except Exception as e:
            logger.error(f"Error getting existing keys: {e}")
            return set()
    
    def upsert_data(
        self, 
        df: pd.DataFrame, 
        table_name: str = "maintainance",
        chunk_size: int = 500
    ) -> Dict:
        """
        Upsert data to database with duplicate detection.
        
        Returns:
            Dict with keys: success, inserted, skipped, error
        """
        result = {
            "success": False,
            "inserted": 0,
            "skipped": 0,
            "error": None
        }
        
        if df.empty:
            result["success"] = True
            return result
        
        try:
            # Ensure table exists
            if not self.table_exists(table_name):
                logger.info(f"Table '{table_name}' does not exist, creating...")
                if not self.create_maintenance_table(table_name):
                    result["error"] = "Failed to create table"
                    return result
            
            # Get existing keys to avoid duplicates
            existing_keys = self.get_existing_keys(table_name)
            
            # Create duplicate key column
            df = df.copy()
            df['_dup_key'] = df['INVOICE'].astype(str) + '|' + df['DESCRIPTION'].astype(str)
            
            # Filter out existing records
            new_records = df[~df['_dup_key'].isin(existing_keys)]
            skipped = len(df) - len(new_records)
            
            result["skipped"] = skipped
            
            if len(new_records) == 0:
                logger.info("No new records to insert")
                result["success"] = True
                return result
            
            # Remove helper column
            new_records = new_records.drop(columns=['_dup_key'])
            
            # Prepare data for insert
            columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
            
            # Ensure all columns exist
            for col in columns:
                if col not in new_records.columns:
                    new_records[col] = None
            
            new_records = new_records[columns]
            
            # Convert date column
            if 'DATE' in new_records.columns:
                new_records['DATE'] = pd.to_datetime(new_records['DATE'], errors='coerce')
            
            # Insert in chunks using INSERT IGNORE to handle any remaining duplicates
            with self.get_connection() as conn:
                from sqlalchemy import text
                
                total_inserted = 0
                
                for i in range(0, len(new_records), chunk_size):
                    chunk = new_records.iloc[i:i + chunk_size]
                    
                    # Build INSERT IGNORE statement
                    placeholders = ', '.join([f":{col}" for col in columns])
                    col_names = ', '.join([f"`{col}`" for col in columns])
                    
                    insert_sql = f"""
                        INSERT IGNORE INTO `{table_name}` ({col_names})
                        VALUES ({placeholders})
                    """
                    
                    # Convert chunk to list of dicts
                    records = chunk.to_dict('records')
                    
                    # Clean up None/NaN values
                    for record in records:
                        for key, value in record.items():
                            if pd.isna(value):
                                record[key] = None
                    
                    # Execute batch insert
                    for record in records:
                        try:
                            conn.execute(text(insert_sql), record)
                            total_inserted += 1
                        except Exception as e:
                            logger.warning(f"Failed to insert record: {e}")
                    
                    conn.commit()
                    logger.info(f"Inserted chunk {i//chunk_size + 1}, total: {total_inserted}")
                
                result["inserted"] = total_inserted
                result["success"] = True
                logger.info(f"Database upsert complete: {total_inserted} inserted, {skipped} skipped")
                
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Database upsert failed: {e}")
        
        return result
    
    def get_record_count(self, table_name: str = "maintainance") -> int:
        """Get total record count in table."""
        try:
            with self.get_connection() as conn:
                from sqlalchemy import text
                result = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`"))
                return result.fetchone()[0]
        except Exception:
            return 0


def check_db_dependencies() -> bool:
    """Check if database dependencies are installed."""
    try:
        import sqlalchemy
        import mysql.connector
        from dotenv import load_dotenv
        return True
    except ImportError:
        return False


# Convenience function for testing
def test_db_connection(env_path: Optional[str] = None) -> bool:
    """Test database connection."""
    try:
        manager = RitaDatabaseManager(env_path)
        return manager.test_connection()
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False
