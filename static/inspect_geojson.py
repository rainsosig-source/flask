import json

def inspect_geojson():
    try:
        with open("countries.geojson", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        targets = ["United States", "Spain", "France", "United States of America"]
        found = {}
        
        for feature in data['features']:
            props = feature['properties']
            name = props.get('ADMIN') or props.get('NAME')
            iso = props.get('ISO_A2')
            
            if name in targets:
                found[name] = iso
                
        print("Found ISO codes:")
        for k, v in found.items():
            print(f"{k}: {v}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_geojson()
