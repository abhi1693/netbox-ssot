import django.db.models.deletion
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def assign_existing_agents(apps, schema_editor):
    DiscoverySource = apps.get_model("netbox_ssot", "DiscoverySource")
    for source in DiscoverySource.objects.all():
        agent = source.agents.filter(enabled=True).order_by("created_at", "pk").first()
        if agent is None:
            agent = source.agents.order_by("created_at", "pk").first()
        if agent is not None:
            source.assigned_agent_id = agent.pk
            source.save(update_fields=("assigned_agent",))


def restore_agent_authorizations(apps, schema_editor):
    DiscoverySource = apps.get_model("netbox_ssot", "DiscoverySource")
    for source in DiscoverySource.objects.exclude(assigned_agent_id=None):
        source.agents.add(source.assigned_agent_id)


class Migration(migrations.Migration):
    dependencies = [("netbox_ssot", "0004_applyrun_applyitem_objectbinding")]

    operations = [
        migrations.AddField(
            model_name="discoverysource",
            name="assigned_agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sources",
                to="netbox_ssot.collectoragent",
            ),
        ),
        migrations.AddField(
            model_name="discoverysource",
            name="collection_interval_minutes",
            field=models.PositiveIntegerField(
                default=60,
                validators=[MinValueValidator(1), MaxValueValidator(43_200)],
            ),
        ),
        migrations.RunPython(assign_existing_agents, restore_agent_authorizations),
        migrations.RemoveField(model_name="collectoragent", name="sources"),
    ]
