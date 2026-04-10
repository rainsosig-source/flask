import json
try:
    with open('/root/flask-app/static/countries.geojson', 'r', encoding='utf-8') as f:
        data = json.load(f)
        print("Keys:", list(data['features'][0]['properties'].keys()))
        print("Example:", data['features'][0]['properties'])
except Exception as e:
    print(e)
