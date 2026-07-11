from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.resumes.models import LatexPackageUsage


class LatexPackageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_usage(self, package_name: str) -> None:
        """Upsert a package usage row.

        If the package already exists, increment its `download_count` and refresh
        `last_used_at`. Otherwise insert a new row with `download_count = 1`.
        """
        stmt = pg_insert(LatexPackageUsage).values(
            package_name=package_name,
            download_count=1,
        ).on_conflict_do_update(
            index_elements=["package_name"],
            set_={
                "download_count": LatexPackageUsage.download_count + 1,
                "last_used_at": func.now(),
            },
        )
        await self.db.execute(stmt)
        await self.db.flush()
