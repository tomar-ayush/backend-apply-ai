import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.conftest import make_user
from app.users.schemas import UpdateUserRequest
from app.users.service import UserService


@pytest.mark.asyncio
async def test_get_me_returns_profile():
    user = make_user(email="alice@example.com", full_name="Alice")
    svc = UserService(db=None)
    profile = await svc.get_me(user)
    assert profile.email == "alice@example.com"
    assert profile.full_name == "Alice"
    assert profile.has_llm_api_key is False


@pytest.mark.asyncio
async def test_get_me_has_llm_key_flag():
    user = make_user(encrypted_llm_api_key="someencryptedvalue")
    svc = UserService(db=None)
    profile = await svc.get_me(user)
    assert profile.has_llm_api_key is True


@pytest.mark.asyncio
async def test_update_me_encrypts_api_key():
    user = make_user()
    db = AsyncMock()
    req = UpdateUserRequest(llm_provider="openai", llm_api_key="sk-test-key")

    with patch("app.users.service.UserRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.update = AsyncMock(return_value=make_user(
            llm_provider="openai",
            encrypted_llm_api_key="encrypted",
        ))
        MockRepo.return_value = mock_repo
        svc = UserService(db=db)
        profile = await svc.update_me(user, req)
        assert mock_repo.update.called
        call_kwargs = mock_repo.update.call_args[1]
        assert "encrypted_llm_api_key" in call_kwargs
        assert call_kwargs["encrypted_llm_api_key"] != "sk-test-key"


@pytest.mark.asyncio
async def test_update_me_does_not_include_plaintext_key():
    user = make_user()
    db = AsyncMock()
    req = UpdateUserRequest(llm_api_key="my-secret-key")

    with patch("app.users.service.UserRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.update = AsyncMock(return_value=make_user())
        MockRepo.return_value = mock_repo
        svc = UserService(db=db)
        await svc.update_me(user, req)
        call_kwargs = mock_repo.update.call_args[1]
        assert "llm_api_key" not in call_kwargs


def test_get_decrypted_llm_key_returns_none_when_not_set():
    user = make_user(encrypted_llm_api_key=None)
    svc = UserService(db=None)
    assert svc.get_decrypted_llm_key(user) is None
