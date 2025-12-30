# fix_templates.py
import os
import re


def fix_templates():
    templates_dir = 'app/templates'

    for root, dirs, files in os.walk(templates_dir):
        for file in files:
            if file.endswith('.html'):
                filepath = os.path.join(root, file)
                with open(filepath, 'r') as f:
                    content = f.read()

                # Fix timestamp[:10] -> format_date filter
                fixed_content = re.sub(
                    r'\{([^}]+?)\.timestamp\[:10\]([^}]*?)\}',
                    r'{\1.timestamp|format_date("%Y-%m-%d")\2}',
                    content
                )

                # Fix timestamp[:19] -> format_date filter
                fixed_content = re.sub(
                    r'\{([^}]+?)\.timestamp\[:19\]([^}]*?)\}',
                    r'{\1.timestamp|format_date\2}',
                    fixed_content
                )

                # Fix any other timestamp slicing
                fixed_content = re.sub(
                    r'\{([^}]+?)\.timestamp\[:(\d+)\]([^}]*?)\}',
                    r'{\1.timestamp|format_date\3}',
                    fixed_content
                )

                if content != fixed_content:
                    with open(filepath, 'w') as f:
                        f.write(fixed_content)
                    print(f"Fixed: {filepath}")


if __name__ == "__main__":
    fix_templates()
    print("Template fixes applied")