import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.referrals.models import ReferralStatus
from app.referrals.schemas import UpdateReferralRequest
from app.referrals.service import ReferralService
from app.common.exceptions import InvalidTransitionError, NotFoundError
from tests.conftest import make_user, make_referral


def test_valid_referral_transitions():
    from app.referrals.models import is_valid_referral_transition
    assert is_valid_referral_transition(ReferralStatus.NOT_CONTACTED, ReferralStatus.REQUESTED)
    assert not is_valid_referral_transition(ReferralStatus.NOT_CONTACTED, ReferralStatus.REFERRED)
    assert is_valid_referral_transition(ReferralStatus.REQUESTED, ReferralStatus.RESPONDED)
    assert is_valid_referral_transition(ReferralStatus.REQUESTED, ReferralStatus.DECLINED)
    assert not is_valid_referral_transition(ReferralStatus.REFERRED, ReferralStatus.REQUESTED)
    assert not is_valid_referral_transition(ReferralStatus.DECLINED, ReferralStatus.RESPONDED)


@pytest.mark.asyncio
async def test_update_referral_not_found():
    db = AsyncMock()
    user = make_user()
    with patch("app.referrals.service.ReferralRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=None)
        MockRepo.return_value = mock_repo
        svc = ReferralService(db)
        req = UpdateReferralRequest(status=ReferralStatus.REQUESTED)
        with pytest.raises(NotFoundError):
            await svc.update(uuid.uuid4(), req, user)


@pytest.mark.asyncio
async def test_update_referral_invalid_transition():
    db = AsyncMock()
    user = make_user()
    referral = make_referral(status=ReferralStatus.DECLINED)
    with patch("app.referrals.service.ReferralRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=referral)
        MockRepo.return_value = mock_repo
        svc = ReferralService(db)
        req = UpdateReferralRequest(status=ReferralStatus.REQUESTED)
        with pytest.raises(InvalidTransitionError):
            await svc.update(referral.id, req, user)


@pytest.mark.asyncio
async def test_update_referral_sets_asked_at():
    db = AsyncMock()
    user = make_user()
    referral = make_referral(status=ReferralStatus.NOT_CONTACTED)
    updated = make_referral(status=ReferralStatus.REQUESTED)
    with patch("app.referrals.service.ReferralRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=referral)
        mock_repo.update = AsyncMock(return_value=updated)
        MockRepo.return_value = mock_repo
        svc = ReferralService(db)
        req = UpdateReferralRequest(status=ReferralStatus.REQUESTED)
        await svc.update(referral.id, req, user)
        call_kwargs = mock_repo.update.call_args[1]
        assert "asked_at" in call_kwargs
        assert call_kwargs["asked_at"] is not None
