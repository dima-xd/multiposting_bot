import mimetypes
import os
import re
from asyncio import Lock
from enum import Enum
from typing import Any, TypedDict, Tuple, List

import pytumblr
import requests
import tweepy
import vk_api
import yaml
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.scene import SceneRegistry, Scene, on, After
from aiogram.types import (
    Message,
    InlineKeyboardButton, CallbackQuery, FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.media_group import MediaGroupBuilder
from atproto_client import Client, models
from atproto_identity import resolver

with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

TOKEN = config["TG_BOT_TOKEN"]

BUTTON_CANCEL = InlineKeyboardButton(text="✖️ Cancel", callback_data="cancel")
BUTTON_BACK = InlineKeyboardButton(text="🔙 Back", callback_data="back")

router = Router(name=__name__)
bot = Bot(token=TOKEN)

MEDIA_DIR = "media"
download_lock = Lock()

class Networks(Enum):
    Telegram = 0
    VK = 1
    Twitter = 2
    Tumblr = 3
    Bluesky = 4
SOCIAL_NETWORKS = [Networks.Telegram.name, Networks.VK.name, Networks.Twitter.name, Networks.Tumblr.name, Networks.Bluesky.name]

def generate_choose_network_keyboard(chosen_networks):
    menu_builder = InlineKeyboardBuilder()
    for network in SOCIAL_NETWORKS:
        state = "✅" if network in chosen_networks else "❌"
        menu_builder.row(
            InlineKeyboardButton(text=f"{state} {network}", callback_data=f"network:{network}")
        )

    menu_builder.row(
        InlineKeyboardButton(text="Finish choosing", callback_data="finish")
    )

    menu_builder.row(
        InlineKeyboardButton(text="Choose All", callback_data="choose_all"),
        InlineKeyboardButton(text="Choose Nothing", callback_data="choose_nothing")
    )

    menu_builder.row(
        BUTTON_BACK,
        BUTTON_CANCEL
    )
    return menu_builder

async def global_error_handler(update, exception):
    import traceback
    traceback.print_exc()
    return True

class FSMData(TypedDict, total=False):
    profile: str
    profile_settings: Any
    networks: list[str]
    russian_text: str
    english_text: str
    clean_tags: set[str]
    tags: str
    bsky_tags: set[str]
    answer_message: Message
    twitter_reply_post: str
    bsky_reply_post: str

def get_media_next_number():
    existing = [f for f in os.listdir(MEDIA_DIR) if f.startswith("media_")]
    numbers = [
        int(f.split("_")[1].split(".")[0])
        for f in existing
        if f.split("_")[1].split(".")[0].isdigit()
    ]
    return max(numbers, default=0) + 1

def get_media_files() -> list[str]:
    if not os.path.exists(MEDIA_DIR):
        return []

    files = [
        os.path.join(MEDIA_DIR, f)
        for f in os.listdir(MEDIA_DIR)
        if os.path.isfile(os.path.join(MEDIA_DIR, f)) and f.startswith("media_")
    ]
    return sorted(files)

def remove_media_files():
    for filename in os.listdir(MEDIA_DIR):
        file_path = os.path.join(MEDIA_DIR, filename)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")

class CancellableScene(Scene,
                       reset_data_on_enter=False,
                       reset_history_on_enter=False,
                       callback_query_without_state=True
                       ):
    @on.callback_query(F.data == "back", after=After.back())
    async def handle_back(self, callback_query: CallbackQuery):
        pass

    @on.callback_query(F.data == "cancel")
    async def handle_cancel(self, callback_query: CallbackQuery):
        await self.wizard.goto(StartScene)

def extract_url_byte_positions(text: str, *, encoding: str = 'UTF-8') -> List[Tuple[str, int, int]]:
    encoded_text = text.encode(encoding)

    pattern = rb'https?://[^ \n\r\t]*'

    matches = re.finditer(pattern, encoded_text)
    url_byte_positions = []

    for match in matches:
        url_bytes = match.group(0)
        url = url_bytes.decode(encoding)
        url_byte_positions.append((url, match.start(), match.end()))

    return url_byte_positions

class SendScene(CancellableScene, state="SendScene"):
    async def upload_to_tg(self, message: Message):
        try:
            data: FSMData = await self.wizard.get_data()
            russian_text = data.get("russian_text")
            profile_settings = data.get("profile_settings")

            media_group = MediaGroupBuilder(caption=russian_text)
            medias = get_media_files()

            for media in medias:
                mime, _ = mimetypes.guess_type(media)
                file = FSInputFile(media)

                if mime.startswith("image/"):
                    media_group.add_photo(media=file)
                elif mime.startswith("video/"):
                    media_group.add_video(media=file)

            if len(medias) > 0:
                await bot.send_media_group(chat_id=profile_settings["TG_CHANNEL_ID"], media=media_group.build())
            else:
                await bot.send_message(chat_id=profile_settings["TG_CHANNEL_ID"], text=russian_text)

            await bot.send_message(chat_id=message.chat.id,
                text="✅ Created TG post"
            )
        except Exception as e:
            await bot.send_message(chat_id=message.chat.id,
                text=f"❌ Failed to create TG post\n{str(e)}"
            )

    async def upload_to_vk(self, message: Message):
        try:
            data: FSMData = await self.wizard.get_data()
            profile_settings = data.get("profile_settings")
            russian_text = data.get("russian_text")
            tags = data.get("tags")

            vk_session = vk_api.VkApi(
                token=profile_settings["VK_TOKEN"])
            vk = vk_session.get_api()
            uploadServer = vk.photos.getWallUploadServer(
                group_id=profile_settings["VK_GROUP_ID"]
            )

            files = get_media_files()
            attachments = []
            for file in files:
                with open(file, "rb") as temp:
                    photo_files = {"photo": temp}
                    upload_response = requests.post(uploadServer["upload_url"], files=photo_files)
                    if upload_response and upload_response.status_code == 200:
                        json_response = upload_response.json()

                        photos_response = vk.photos.saveWallPhoto(
                            photo=json_response["photo"],
                            server=json_response["server"],
                            hash=json_response["hash"],
                            group_id=profile_settings["VK_GROUP_ID"],
                            caption=tags
                        )

                        attachments.append(f"photo{photos_response[0]['owner_id']}_{photos_response[0]['id']}")

            post_response = vk.wall.post(
                owner_id=-1 * profile_settings["VK_GROUP_ID"],
                message=russian_text,
                attachments=attachments,
                from_group=1
            )
            if post_response['post_id']:
                await bot.send_message(chat_id=message.chat.id,
                    text=f"✅ Created VK post: https://vk.com/wall-{profile_settings["VK_GROUP_ID"]}_{post_response['post_id']}"
                )
        except Exception as e:
            await bot.send_message(chat_id=message.chat.id,
                text=f"❌ Failed to create VK post\n{str(e)}"
            )

    async def upload_to_twitter(self, message: Message):
        try:
            media_ids = []

            data: FSMData = await self.wizard.get_data()
            profile_settings = data.get("profile_settings")
            english_text = data.get("english_text")
            tags = data.get("tags")
            twitter_reply_post = data.get("twitter_reply_post")

            twitter_auth = tweepy.OAuth1UserHandler(
                profile_settings["TWITTER_CONSUMER_KEY"],
                profile_settings["TWITTER_CONSUMER_SECRET"],
                profile_settings["TWITTER_ACCESS_TOKEN"],
                profile_settings["TWITTER_ACCESS_SECRET"]
            )

            twitter_api = tweepy.API(twitter_auth)

            client = tweepy.Client(consumer_key=profile_settings["TWITTER_CONSUMER_KEY"],
                                   consumer_secret=profile_settings["TWITTER_CONSUMER_SECRET"],
                                   access_token=profile_settings["TWITTER_ACCESS_TOKEN"],
                                   access_token_secret=profile_settings["TWITTER_ACCESS_SECRET"])

            files = get_media_files()
            for file in files:
                media = twitter_api.media_upload(filename=file)
                media_ids.append(media.media_id)

            text = english_text
            if tags != "":
                text += "\n\n" + tags

            reply_id = None
            if twitter_reply_post:
                reply_id = twitter_reply_post.split("/")[-1]

            if len(media_ids) > 0:
                tweet_post = client.create_tweet(text=text, media_ids=media_ids, in_reply_to_tweet_id=reply_id)
            else:
                tweet_post = client.create_tweet(text=text, in_reply_to_tweet_id=reply_id)
            if tweet_post:
                my_twitter = client.get_me(user_auth=True)
                await bot.send_message(chat_id=message.chat.id,
                    text=f"✅ Created Twitter post: https://x.com/{my_twitter.data['username']}/status/{tweet_post.data['id']}"
                )
        except Exception as e:
            await bot.send_message(chat_id=message.chat.id,
                text=f"❌ Failed to create Twitter post\n{str(e)}",
            )

    async def upload_to_tumblr(self, message: Message):
        try:
            def format_links(text: str) -> str:
                url_pattern = r"https?://[^\s\]\)]+"
                return re.sub(url_pattern, lambda m: f"[{m.group(0)}]({m.group(0)})", text)

            data: FSMData = await self.wizard.get_data()
            profile_settings = data.get("profile_settings")
            english_text = data.get("english_text")
            clean_tags = data.get("clean_tags")

            tumblr_api = pytumblr.TumblrRestClient(
                profile_settings["TUMBLR_CONSUMER_KEY"],
                profile_settings["TUMBLR_CONSUMER_SECRET"],
                profile_settings["TUMBLR_ACCESS_TOKEN"],
                profile_settings["TUMBLR_ACCESS_SECRET"]
            )

            tumblr_info = tumblr_api.info()
            tumblr_user = tumblr_info['user']['name']

            files = get_media_files()
            if len(files) > 0:
                mime, _ = mimetypes.guess_type(files[-1])
                if mime.startswith("image/"):
                    tumblr_response = tumblr_api.create_photo(tumblr_user, tags=clean_tags,
                                                              caption=format_links(english_text),
                                                              format="markdown",
                                                              data=files)
                elif mime.startswith("video/"):
                    tumblr_response = tumblr_api.create_video(tumblr_user, tags=clean_tags,
                                                              caption=format_links(english_text),
                                                              format="markdown",
                                                              data=files)
            else:
                tumblr_response = tumblr_api.create_text(tumblr_user, tags=clean_tags,
                                                         body=format_links(english_text))
            if tumblr_response and tumblr_response['id']:
                tumblr_url = f"https://tumblr.com/{tumblr_user}/{tumblr_response['id']}"
                await bot.send_message(chat_id=message.chat.id,
                    text=f"✅ Created Tumblr post: {tumblr_url}"
                )

        except Exception as e:
            await bot.send_message(chat_id=message.chat.id, text=f"❌ Failed to create Tumblr post\n{str(e)}")

    async def upload_to_bsky(self, message: Message):
        try:
            data: FSMData = await self.wizard.get_data()
            profile_settings = data.get("profile_settings")
            english_text = data.get("english_text")
            bsky_tags = data.get("bsky_tags")
            bsky_reply_post = data.get("bsky_reply_post")

            embeds = []

            bluesky_api = Client()
            bluesky_api.login(profile_settings["BLUESKY_LOGIN"], profile_settings["BLUESKY_PASSWORD"])

            files = get_media_files()
            is_video = False
            for file in files:
                with open(file, "rb+") as f:
                    f.seek(0)
                    blob = f.read()

                    mime, _ = mimetypes.guess_type(file)
                    uploaded_blob = bluesky_api.upload_blob(blob).blob

                    if mime.startswith("image/"):
                        embeds.append(models.AppBskyEmbedImages.Image(
                                        image=uploaded_blob,
                                        alt="",
                                        aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=1, height=1),
                        ))
                    elif mime.startswith("video/"):
                        embeds.append(models.AppBskyEmbedVideo.Main(
                            video=uploaded_blob,
                            alt="",
                            aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(width=1, height=1),
                        ))
                        is_video = True

            facets = []
            for hashtag in bsky_tags:
                facets.append(models.AppBskyRichtextFacet.Main(
                    features=[models.AppBskyRichtextFacet.Tag(tag=hashtag)],
                    index=models.AppBskyRichtextFacet.ByteSlice(byte_start=len(english_text),
                                                                byte_end=len(english_text)))
                )

            url_positions = extract_url_byte_positions(english_text)
            for link_data in url_positions:
                uri, byte_start, byte_end = link_data
                facets.append(
                    models.AppBskyRichtextFacet.Main(
                        features=[models.AppBskyRichtextFacet.Link(uri=uri)],
                        index=models.AppBskyRichtextFacet.ByteSlice(byte_start=byte_start, byte_end=byte_end),
                    )
                )

            reply_ref = None
            if bsky_reply_post:
                url_parts = bsky_reply_post.split('/')
                handle = url_parts[4]
                post_rkey = url_parts[6]

                did = resolver.IdResolver().handle.resolve(handle)
                if not did:
                    await bot.send_message(chat_id=message.chat.id, text=f'Could not resolve DID for handle "{handle}".')
                    return

                response = bluesky_api.get_post(post_rkey, did)

                record_ref = models.ComAtprotoRepoStrongRef.Main(
                    cid=response.cid,
                    uri=response.uri
                )
                reply_ref = models.AppBskyFeedPost.ReplyRef(
                    parent=record_ref, root=record_ref
                )
            if not is_video:
                bluesky_response = bluesky_api.send_post(text=english_text, langs=["en-US"],
                                                         embed=models.AppBskyEmbedImages.Main(
                                                             images=embeds
                                                         ),
                                                         reply_to=reply_ref,
                                                         facets=facets
                )
            else:
                bluesky_response = bluesky_api.send_post(text=english_text, langs=["en-US"],
                                                         embed=embeds[0],
                                                         facets=facets
                )
            if bluesky_response and bluesky_response["uri"]:
                at_uri = bluesky_response["uri"]
                parts = at_uri[5:].split("/")
                if len(parts) == 3:
                    did, collection, rkey = parts

                    if collection == "app.bsky.feed.post":
                        bluesky_url = f"https://bsky.app/profile/{did}/post/{rkey}"
                        await bot.send_message(chat_id=message.chat.id, text=f"✅ Created Bluesky post: {bluesky_url}")
        except Exception as e:
            await bot.send_message(chat_id=message.chat.id,
                text=f"❌ Failed to create Bluesky post\n{str(e)}"
            )

    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")

        if isinstance(event, CallbackQuery):
            message = event.message
        else:
            message = event

        if Networks.Telegram.name in networks:
            await self.upload_to_tg(message)
        if Networks.VK.name in networks:
            await self.upload_to_vk(message)
        if Networks.Twitter.name in networks:
            await self.upload_to_twitter(message)
        if Networks.Tumblr.name in networks:
            await self.upload_to_tumblr(message)
        if Networks.Bluesky.name in networks:
            await self.upload_to_bsky(message)

        remove_media_files()
        await self.wizard.update_data(answer_message=None)

class PicturesScene(CancellableScene, state="pictures"):
    async def message_enter(self, message: Message):
        data: FSMData = await self.wizard.get_data()
        answer_message = data.get("answer_message")

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_pictures"),
            InlineKeyboardButton(text="Finish", callback_data="finish_sending")
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Send Pictures/Videos for your post (Maximum 4):",
            reply_markup=menu_builder.as_markup()
        )

    @on.callback_query(F.data == "skip_pictures")
    async def skip_callback(self, callback_query: CallbackQuery):
        os.remove(MEDIA_DIR)
        os.makedirs(MEDIA_DIR, exist_ok=True)

        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(TwitterReplyScene)

    @on.callback_query(F.data == "finish_sending")
    async def finish_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(TwitterReplyScene)

    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        if isinstance(event, CallbackQuery):
            if event.data == "skip_pictures":
                await self.skip_callback(event)
                return
            await self.message_enter(event.message)
        else:
            await self.message_enter(event)

    @on.message()
    async def on_media_choose(self, message: Message):
        async with download_lock:
            try:
                data: FSMData = await self.wizard.get_data()
                answer_message = data.get("answer_message")
                networks = data.get("networks")

                os.makedirs(MEDIA_DIR, exist_ok=True)

                file_id = -1
                target_path = ""
                number_of_medias = get_media_next_number()

                menu_builder = InlineKeyboardBuilder()
                menu_builder.row(
                    InlineKeyboardButton(text="Skip", callback_data="skip_pictures"),
                    InlineKeyboardButton(text="Finish", callback_data="finish_sending")
                )
                menu_builder.row(
                    BUTTON_BACK,
                    BUTTON_CANCEL
                )

                if number_of_medias >= 4 and networks != ["Telegram"]:
                    await answer_message.edit_text(
                        f"Can't add more medias. Maximum is 4",
                        reply_markup=menu_builder.as_markup()
                    )
                    return

                if message.document:
                    filename = message.document.file_name
                    ext = os.path.splitext(filename)[1] or ""

                    target_filename = f"media_{number_of_medias}{ext}"
                    target_path = os.path.join(MEDIA_DIR, target_filename)

                    file_id = message.document.file_id
                elif message.photo:
                    filename = "media.jpg"
                    ext = os.path.splitext(filename)[1] or ""

                    target_filename = f"media_{number_of_medias}{ext}"
                    target_path = os.path.join(MEDIA_DIR, target_filename)

                    file_id = message.photo[-1].file_id
                elif message.video:
                    filename = message.video.file_name
                    ext = os.path.splitext(filename)[1] or ""

                    target_filename = f"media_{number_of_medias}{ext}"
                    target_path = os.path.join(MEDIA_DIR, target_filename)

                    file_id = message.video.file_id

                file = await bot.get_file(file_id=file_id)
                await bot.download_file(file_path=file.file_path, destination=target_path)
                await message.answer(
                    f"Added media"
                )
            except Exception as e:
                await bot.send_message(chat_id=message.chat.id, text=str(e))

class TagsScene(CancellableScene, state="tags"):
    async def message_enter(self, message: Message):
        data: FSMData = await self.wizard.get_data()
        answer_message = data.get("answer_message")

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_tags"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Choose tags:",
            reply_markup=menu_builder.as_markup()
        )

    @on.callback_query(F.data == "skip_tags")
    async def skip_callback(self, callback_query: CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")

        await callback_query.message.edit_reply_markup(reply_markup=None)
        if Networks.Bluesky.name not in networks:
            await self.wizard.goto(PicturesScene)
        else:
            await self.wizard.goto(HiddenBskyTagsScene)

    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        remove_media_files()
        if isinstance(event, CallbackQuery):
            if event.data == "skip_tags":
                await self.skip_callback(event)
                return
            await self.message_enter(event.message)
        else:
            await self.message_enter(event)

    @on.message()
    async def on_tags_choice(self, message: Message):
        unique_words = set(word.strip("#,") for word in message.text.split())
        unique_words = sorted(unique_words)

        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")

        clean_tags = unique_words
        tags = ", ".join([f"#{word}" for word in unique_words])

        await self.wizard.update_data(clean_tags=clean_tags)
        await self.wizard.update_data(tags=tags)

        await message.answer(
            f"Added {len(clean_tags)} tags"
        )
        await message.answer(
            f"```Hashtags {", ".join([f"{word}" for word in unique_words])}```",
            parse_mode="MarkdownV2"
        )

        if Networks.Bluesky.name not in networks:
            await self.wizard.goto(PicturesScene)
        else:
            await self.wizard.goto(HiddenBskyTagsScene)

class HiddenBskyTagsScene(CancellableScene, state="bsky_tags"):
    async def message_enter(self, message: Message):
        data: FSMData = await self.wizard.get_data()
        answer_message = data.get("answer_message")

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_bsky_tags"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Choose Bluesky hidden tags:",
            reply_markup=menu_builder.as_markup()
        )

    @on.callback_query(F.data == "skip_bsky_tags")
    async def skip_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(PicturesScene)

    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        remove_media_files()
        if isinstance(event, CallbackQuery):
            if event.data == "skip_bsky_tags":
                await self.skip_callback(event)
                return
            await self.message_enter(event.message)
        else:
            await self.message_enter(event)

    @on.message()
    async def on_tags_choice(self, message: Message):
        unique_words = set(word.strip("#,") for word in message.text.split())
        unique_words = sorted(unique_words)

        await self.wizard.update_data(bsky_tags=unique_words)

        await message.answer(
            f"Added {len(unique_words)} Bluesky tags"
        )
        await message.answer(
            f"```Hashtags {", ".join([f"{word}" for word in unique_words])}```",
            parse_mode="MarkdownV2"
        )

        await self.wizard.goto(PicturesScene)

class EnglishTextScene(CancellableScene, state="english_text"):
    async def message_enter(self, message: Message):
        data: FSMData = await self.wizard.get_data()
        answer_message = data.get("answer_message")

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_english_text"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Type post (in English):",
            reply_markup=menu_builder.as_markup()
        )

    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        if isinstance(event, CallbackQuery):
            if event.data == "skip_english_text":
                await self.skip_callback(event)
                return

            await self.message_enter(event.message)
        else:
            await self.message_enter(event)

    @on.callback_query(F.data == "skip_english_text")
    async def skip_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(TagsScene)

    @on.message()
    async def on_english_text_choice(self, message: Message):
        await self.wizard.update_data(english_text=message.text)
        await message.delete()
        await self.wizard.goto(TagsScene)

class RussianTextScene(CancellableScene, state="russian_text"):
    @on.callback_query.enter()
    async def on_enter_callback(self, callback_query: CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        answer_message = data.get("answer_message")

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_russian_text"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Type post (in Russian):",
            reply_markup=menu_builder.as_markup()
        )

    @on.message()
    async def on_russian_text_choice(self, message: Message):
        await self.wizard.update_data(russian_text=message.text)
        await message.delete()
        await self.wizard.goto(EnglishTextScene)

    @on.callback_query(F.data == "skip_russian_text")
    async def skip_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(EnglishTextScene)

class TwitterReplyScene(CancellableScene, state="twitter_reply"):
    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")
        answer_message = data.get("answer_message")

        if Networks.Twitter.name not in networks:
            await self.wizard.goto(BskyReplyScene)
            return

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_twitter_reply"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Link Twitter post if you want to reply:",
            reply_markup=menu_builder.as_markup()
        )

    @on.message()
    async def on_twitter_reply_choice(self, message: Message):
        await self.wizard.update_data(twitter_reply_post=message.text)
        await message.delete()
        await self.wizard.goto(BskyReplyScene)

    @on.callback_query(F.data == "skip_twitter_reply")
    async def skip_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(BskyReplyScene)

class BskyReplyScene(CancellableScene, state="bsky_reply"):
    @on.callback_query.enter()
    @on.message.enter()
    async def on_enter_callback(self, event: Message | CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")
        answer_message = data.get("answer_message")

        if Networks.Bluesky.name not in networks:
            await self.wizard.goto(SendScene)
            return

        menu_builder = InlineKeyboardBuilder()
        menu_builder.row(
            InlineKeyboardButton(text="Skip", callback_data="skip_bsky_reply"),
        )
        menu_builder.row(
            BUTTON_BACK,
            BUTTON_CANCEL
        )

        await answer_message.edit_text(
            "Link Bluesky post if you want to reply:",
            reply_markup=menu_builder.as_markup()
        )

    @on.message()
    async def on_bsky_reply_choice(self, message: Message):
        await self.wizard.update_data(bsky_reply_post=message.text)
        await message.delete()
        await self.wizard.goto(SendScene)

    @on.callback_query(F.data == "skip_bsky_reply")
    async def skip_callback(self, callback_query: CallbackQuery):
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await self.wizard.goto(SendScene)

class SocialNetworkScene(CancellableScene, state="social_network"):
    @on.callback_query.enter()
    async def on_enter_callback(self, callback_query: CallbackQuery):
        data: FSMData = await self.wizard.get_data()
        networks = data.get("networks")
        profile = data.get("profile")
        answer_message = data.get("answer_message")

        await answer_message.edit_text(
            f"Choose social networks for {profile}:",
            reply_markup=generate_choose_network_keyboard(networks).as_markup(),
        )

    @on.callback_query(F.data.startswith("network:"))
    async def network_callback(self, callback_query: CallbackQuery):
        try:
            data: FSMData = await self.wizard.get_data()
            networks = data.get("networks")

            networks_data = callback_query.data.split(":")
            network = networks_data[-1]

            if network in networks:
                networks.remove(network)
            else:
                networks.append(network)

            await self.wizard.update_data(networks=networks)

            new_keyboard = generate_choose_network_keyboard(networks).as_markup()

            await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
            await callback_query.answer()
        except Exception as e:
            print(e)

    @on.callback_query(F.data == "choose_all")
    async def choose_all_callback(self, callback_query: CallbackQuery):
        try:
            data: FSMData = await self.wizard.get_data()
            networks = data.get("networks")

            for network in SOCIAL_NETWORKS:
                if network not in networks:
                    networks.append(network)

            await self.wizard.update_data(networks=networks)

            new_keyboard = generate_choose_network_keyboard(networks).as_markup()

            await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
            await callback_query.answer()
        except Exception as e:
            print(e)

    @on.callback_query(F.data == "choose_nothing")
    async def choose_nothing_callback(self, callback_query: CallbackQuery):
        try:
            data: FSMData = await self.wizard.get_data()
            networks = data.get("networks")

            for network in SOCIAL_NETWORKS:
                if network in networks:
                    networks.remove(network)

            await self.wizard.update_data(networks=networks)

            new_keyboard = generate_choose_network_keyboard(networks).as_markup()

            await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
            await callback_query.answer()
        except Exception as e:
            print(e)

    @on.callback_query(F.data == "finish")
    async def finish_callback(self, callback_query: CallbackQuery):
        try:
            data: FSMData = await self.wizard.get_data()
            networks = data.get("networks")
            if len(networks) > 0:
                await self.wizard.goto(RussianTextScene)
        except Exception as e:
            print(e)

class StartScene(CancellableScene, state="start"):
    async def set_default_data(self):
        await self.wizard.update_data(profile="")
        await self.wizard.update_data(networks=[])
        await self.wizard.update_data(tags="")
        await self.wizard.update_data(english_text="")
        await self.wizard.update_data(russian_text="")
        await self.wizard.update_data(clean_tags=[])
        await self.wizard.update_data(bsky_tags="")

    @on.callback_query.enter()
    @on.message.enter()
    @on.message(Command("start"))
    async def on_enter(self, message: Message | CallbackQuery):
        if str(message.from_user.id) not in config["admins"]:
            return

        try:
            if isinstance(message, CallbackQuery):
                if message.message.text == "/start":
                    await self.wizard.update_data(answer_message=None)
            else:
                if message.text == "/start":
                    await self.wizard.update_data(answer_message=None)

            data: FSMData = await self.wizard.get_data()
            answer_message = data.get("answer_message")

            await self.set_default_data()

            profiles = list(config['profiles'].keys())

            menu_builder = InlineKeyboardBuilder()
            for profile in profiles:
                menu_builder.add(InlineKeyboardButton(text=f"{profile}",
                                                      callback_data=f"profile:{profile}"))
            menu_builder.adjust(1, 1)

            if not answer_message:
                    answer_message = await message.answer(text="Choose profile",
                                                          reply_markup=menu_builder.as_markup(),
                                                          )
                    await self.wizard.update_data(answer_message=answer_message)
            else:
                await answer_message.edit_text(text="Choose profile",
                                               reply_markup=menu_builder.as_markup(),
                                               )
        except Exception as e:
            print(e)

    @on.callback_query(F.data.startswith("profile:"), after=After.goto(SocialNetworkScene))
    async def profile_callback(self, callback_query: CallbackQuery):
        try:
            profile_data = callback_query.data.split(":")
            profile = profile_data[-1]
            await self.wizard.update_data(profile=profile)
            await self.wizard.update_data(profile_settings=config["profiles"][profile])
        except Exception as e:
            print(e)

def main() -> None:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    remove_media_files()

    dp = Dispatcher()
    print("Server started")

    dp.message.register(StartScene.as_handler(), Command("start"))
    dp.errors.register(global_error_handler)

    scene_registry = SceneRegistry(dp)
    scene_registry.add(StartScene)
    scene_registry.add(SocialNetworkScene)
    scene_registry.add(RussianTextScene)
    scene_registry.add(EnglishTextScene)
    scene_registry.add(TagsScene)
    scene_registry.add(PicturesScene)
    scene_registry.add(SendScene)
    scene_registry.add(HiddenBskyTagsScene)
    scene_registry.add(TwitterReplyScene)
    scene_registry.add(BskyReplyScene)
    dp.include_router(router)

    dp.run_polling(bot)

if __name__ == "__main__":
    main()