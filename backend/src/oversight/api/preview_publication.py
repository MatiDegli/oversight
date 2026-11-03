"""Install an awaited, committed status sink during router construction, before startup."""

from oversight.api.previews import public_status
from oversight.events import EventStore
from oversight.workspace import PreviewManager, PreviewStatusRecord


def install_preview_publication(store: EventStore, previews: PreviewManager) -> None:
    async def publish(record: PreviewStatusRecord) -> None:
        await store.publish(public_status(record))

    previews.set_status_sink(publish)
