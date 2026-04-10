import pymysql

DB_HOST = 'localhost'
DB_USER = 'root'
DB_PASS = '***REMOVED***'
DB_NAME = 'podcast'

def delete_skip_me():
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, db=DB_NAME, charset='utf8mb4')
    try:
        with conn.cursor() as cursor:
            # Check if it exists first
            cursor.execute("SELECT id, keyword FROM keywords WHERE keyword = 'SKIP_ME'")
            result = cursor.fetchall()
            
            if result:
                print(f"Found {len(result)} 'SKIP_ME' entries. Deleting...")
                cursor.execute("DELETE FROM keywords WHERE keyword = 'SKIP_ME'")
                conn.commit()
                print("Deleted successfully.")
            else:
                print("'SKIP_ME' not found.")
                
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    delete_skip_me()
