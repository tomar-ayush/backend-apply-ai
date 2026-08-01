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
    user = make_user(openai_llm_api_key="someencryptedvalue")
    svc = UserService(db=None)
    profile = await svc.get_me(user)
    assert profile.has_llm_api_key is True


@pytest.mark.asyncio
async def test_update_me_encrypts_api_key():
    user = make_user()
    db = AsyncMock()
    req = UpdateUserRequest(
        llm_provider="openai", llm_api_key="sk-test-key"
    )

    with patch("app.users.service.UserRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.update = AsyncMock(
            return_value=make_user(
                llm_provider="openai",
                openai_llm_api_key="encrypted",
            )
        )
        MockRepo.return_value = mock_repo
        svc = UserService(db=db)
        profile = await svc.update_me(user, req)
        assert mock_repo.update.called
        call_kwargs = mock_repo.update.call_args[1]
        assert "openai_llm_api_key" in call_kwargs
        assert call_kwargs["openai_llm_api_key"] != "sk-test-key"


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
    user = make_user(openai_llm_api_key=None)
    svc = UserService(db=None)
    assert svc.get_decrypted_llm_key(user) is None


def test_get_decrypted_llm_key_returns_none_for_unknown_provider():
    user = make_user(llm_provider="unknown", openai_llm_api_key="enc")
    svc = UserService(db=None)
    assert svc.get_decrypted_llm_key(user) is None


@pytest.mark.asyncio
async def test_update_me_requires_provider_for_key():
    user = make_user(llm_provider=None)
    db = AsyncMock()
    req = UpdateUserRequest(llm_api_key="my-secret-key")
    svc = UserService(db=db)
    import pytest
    from app.common.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await svc.update_me(user, req)


@pytest.mark.asyncio
async def test_update_linkedin_message_success():
    user = make_user()
    db = AsyncMock()
    msg = "Hi! I noticed your team is hiring and would love to connect."

    with patch("app.users.service.UserRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.update = AsyncMock(
            return_value=make_user(linkedin_message=msg)
        )
        MockRepo.return_value = mock_repo
        svc = UserService(db=db)
        profile = await svc.update_linkedin_message(user, msg)
        assert profile.linkedin_message == msg
        mock_repo.update.assert_called_once_with(user, linkedin_message=msg)


@pytest.mark.asyncio
async def test_update_linkedin_message_rejects_empty():
    user = make_user()
    db = AsyncMock()
    svc = UserService(db=db)
    from app.common.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await svc.update_linkedin_message(user, "   ")
