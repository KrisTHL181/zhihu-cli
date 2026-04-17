import json
import sys

def convert_zhihu_to_assets_format(input_file, output_file):
    """
    Convert zhihu_user_activities.json format to all_assets_list.json format.
    """
    try:
        # Read the input JSON file
        with open(input_file, 'r', encoding='utf-8') as f:
            activities = json.load(f)
        
        # Convert each activity to the target format
        converted_data = []
        for activity in activities:
            converted_item = {
                "id": activity.get("id", ""),
                "type": activity.get("type", ""),
                "title": activity.get("title", "")
            }
            converted_data.append(converted_item)
        
        # Write to output file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(converted_data, f, ensure_ascii=False, indent=4)
        
        print(f"Successfully converted {len(converted_data)} items")
        print(f"Output written to: {output_file}")
        
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in input file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

def main():
    # Set default file names
    input_file = "zhihu_user_activities.json"
    output_file = "all_assets_list.json"
    
    # Allow command-line arguments for custom file names
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    
    convert_zhihu_to_assets_format(input_file, output_file)

if __name__ == "__main__":
    main()