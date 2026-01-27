#!/usr/bin/env python3
"""
Systematically update master.html to support Basic/Expert modes.
This script will insert conditionals in the right places.
"""

with open('admin_app/admin_app/templates/master.html', 'r') as f:
    content = f.read()

# First, let's just add the navigation button simplification
# This is the safest change to start with

# Find and replace the navigation buttons section
old_nav = '''      <div class="actions">
        <a href="{{ url_for('campaign_detail', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Campaign</button></a>
        <a href="{{ url_for('results', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Results</button></a>
        <a href="{{ url_for('campaign_recipients', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Recipients</button></a>
        {% if campaign['picker_strategy'] == 'online_assign' %}
          <a href="{{ url_for('campaign_invitations', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Invitations</button></a>
          <a href="{{ url_for('online_stats', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Online stats</button></a>
          <a href="{{ url_for('reports', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Reports</button></a>
        {% endif %}
      </div>'''

new_nav = '''      <div class="actions">
        {% if ui_mode == 'basic' %}
        <a href="{{ url_for('results', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Results</button></a>
        <a href="{{ url_for('campaign_recipients', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Recipients</button></a>
        {% else %}
        <a href="{{ url_for('campaign_detail', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Campaign</button></a>
        <a href="{{ url_for('results', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Results</button></a>
        <a href="{{ url_for('campaign_recipients', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Recipients</button></a>
        {% if campaign['picker_strategy'] == 'online_assign' %}
          <a href="{{ url_for('campaign_invitations', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Invitations</button></a>
          <a href="{{ url_for('online_stats', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Online stats</button></a>
          <a href="{{ url_for('reports', campaign_key=campaign['campaign_key']) }}"><button class="secondary" type="button">Reports</button></a>
        {% endif %}
        {% endif %}
      </div>'''

if old_nav in content:
    content = content.replace(old_nav, new_nav, 1)
    print("✅ Updated navigation")
else:
    print("❌ Navigation section not found")

with open('admin_app/admin_app/templates/master.html', 'w') as f:
    f.write(content)

print("Done!")
