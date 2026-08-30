from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label="SSot",
    icon_class="mdi mdi-sync",
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
                    link="plugins:netbox_ssot:reconciliation_list",
                    link_text="Reconciliations",
                    permissions=("netbox_ssot.view_collectionrun",),
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
                PluginMenuItem(
                    link="plugins:netbox_ssot:provider_list",
                    link_text="Providers",
                    auth_required=True,
                ),
            ),
        ),
    ),
)
