from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label="Discovery",
    icon_class="mdi mdi-radar",
    groups=(
        (
            "Workspace",
            (
                PluginMenuItem(
                    link="plugins:netbox_ssot:overview",
                    link_text="Overview",
                    auth_required=True,
                ),
                PluginMenuItem(
                    link="plugins:netbox_ssot:source_list",
                    link_text="Sources",
                    permissions=("netbox_ssot.view_discoverysource",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_ssot:activity",
                    link_text="Activity",
                    auth_required=True,
                ),
            ),
        ),
        (
            "Setup",
            (
                PluginMenuItem(
                    link="plugins:netbox_ssot:agent_list",
                    link_text="Agents",
                    permissions=("netbox_ssot.view_collectoragent",),
                ),
            ),
        ),
    ),
)
