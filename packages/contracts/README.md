# NetBox SSoT contracts

This package is the stable, NetBox-independent boundary shared by the plugin, remote agent, and provider distributions. It intentionally has no Django, NetBox, DiffSync, or vendor SDK dependency.

The public contract is versioned independently from any implementation package. Breaking changes require a new contract version and a compatibility path.

Dataset declarations can include explicit data-model mappings from provider-native source models to canonical resource
kinds. Destinations resolve those canonical kinds to their installed models, so providers do not embed destination-
specific model names or URLs.
Providers may also identify a non-secret URI configuration field and add safe relative UI paths to individual model
mappings. Destinations can then link to the corresponding source-instance lists without storing an absolute URL in the
shared contract; mappings without this metadata remain plain text.
