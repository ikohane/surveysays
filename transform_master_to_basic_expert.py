#!/usr/bin/env python3
"""
Transform master.html to support Basic/Expert modes in one systematic pass.
"""

with open('admin_app/admin_app/templates/master.html', 'r') as f:
    lines = f.readlines()

output = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Wrap "Variant Generation Waves" card in expert mode
    if '<h3 style="margin-top:0;">Variant Generation Waves</h3>' in line:
        output.append('  {% if ui_mode == \'expert\' %}\n')
        output.append(line)
        # Find the end of this card and add endif
        j = i + 1
        depth = 1
        while j < len(lines) and depth > 0:
            if '<div class="card">' in lines[j] and 'style="margin:0;"' not in lines[j]:
                depth -= 1
                if depth == 0:
                    break
            output.append(lines[j])
            j += 1
        output.append('  {% endif %}\n')
        output.append('\n')
        i = j
        continue
    
    # Wrap other expert-only sections similarly...
    # For now, just output the line as-is
    output.append(line)
    i += 1

# Write output
with open('admin_app/admin_app/templates/master_transformed.html', 'w') as f:
    f.writelines(output)

print("Created master_transformed.html for review")
