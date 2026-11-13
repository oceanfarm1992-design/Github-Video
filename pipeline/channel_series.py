"""Shorts for the sister channels: "5 AI tools for <need>", one need per video.

Same rules as the main AI-tools series (pipeline/tools.py): the catalog is only names + official homepages;
what the video says about a tool comes from that tool's own homepage at build time, and a tool whose site
does not load, has no description or cannot be shown in the browser is swapped for the next entry.

AI for Kids is made FOR PARENTS AND TEACHERS (not "made for kids" on YouTube): learning, creativity and
first steps in AI for children, presented to the adults who choose the tools.
AI for Normal People: everyday tasks for people who are not techies.
"""
import logging
import time

from . import channels, config, publish, tools

log = logging.getLogger("channel_series")
REUSE_DAYS = 60  # a Short every 2 days per channel: each need comes back after about a month of other needs

# category -> (spoken phrase after "AI tools for", hashtag, [(name, homepage), ...])
KIDS = {
    "drawing": ("kids who love to draw", "#KidsArt", [
        ("Quick, Draw!", "https://quickdraw.withgoogle.com"), ("AutoDraw", "https://www.autodraw.com"),
        ("Animated Drawings", "https://sketch.metademolab.com"), ("Canva for Education", "https://www.canva.com/education/"),
        ("Chrome Music Lab", "https://musiclab.chromeexperiments.com"),
        ("Adobe Express", "https://www.adobe.com/express/"), ("Drawing Desk", "https://drawingdesk.com"),
        ("Storybird", "https://storybird.com")]),
    "reading": ("kids learning to read", "#KidsReading", [
        ("Google Read Along", "https://readalong.google"), ("Khan Academy Kids", "https://learn.khanacademy.org/khan-academy-kids/"),
        ("Epic", "https://www.getepic.com"), ("Duolingo ABC", "https://www.duolingo.com/abc"),
        ("Reading Eggs", "https://readingeggs.com"), ("Microsoft Reading Coach", "https://coach.microsoft.com"),
        ("Starfall", "https://www.starfall.com"), ("ABCmouse", "https://www.abcmouse.com"),
        ("Readability", "https://www.readabilitytutor.com")]),
    "coding": ("kids learning to code", "#KidsCoding", [
        ("Scratch", "https://scratch.mit.edu"), ("Code.org", "https://code.org"),
        ("Teachable Machine", "https://teachablemachine.withgoogle.com"),
        ("Machine Learning for Kids", "https://machinelearningforkids.co.uk"), ("Tynker", "https://www.tynker.com"),
        ("ScratchJr", "https://www.scratchjr.org"), ("CodeMonkey", "https://www.codemonkey.com"),
        ("Tinkercad", "https://www.tinkercad.com"), ("Kodable", "https://www.kodable.com"),
        ("Swift Playgrounds", "https://www.apple.com/swift/playgrounds/")]),
    "homework": ("homework help with kids", "#Homework", [
        ("Khanmigo", "https://www.khanmigo.ai"), ("Photomath", "https://photomath.com"), ("Prodigy", "https://www.prodigygame.com"),
        ("Wolfram Alpha", "https://www.wolframalpha.com"), ("Quizlet", "https://quizlet.com"),
        ("Google Lens", "https://lens.google"), ("GeoGebra", "https://www.geogebra.org"), ("Desmos", "https://www.desmos.com")]),
    "teachers": ("teachers", "#TeacherTools", [
        ("MagicSchool", "https://www.magicschool.ai"), ("Brisk Teaching", "https://www.briskteaching.com"),
        ("Diffit", "https://web.diffit.me"), ("Curipod", "https://curipod.com"), ("SchoolAI", "https://schoolai.com"),
        ("Eduaide", "https://www.eduaide.ai"), ("Khanmigo for Teachers", "https://www.khanmigo.ai/teachers"),
        ("Canva for Education", "https://www.canva.com/education/"), ("Quizizz", "https://quizizz.com")]),
    "learn-ai": ("teaching kids what AI is", "#AIForKids", [
        ("Day of AI", "https://dayofai.org"), ("AI for Oceans", "https://code.org/oceans"),
        ("Teachable Machine", "https://teachablemachine.withgoogle.com"), ("Experiments with Google", "https://experiments.withgoogle.com"),
        ("Quick, Draw!", "https://quickdraw.withgoogle.com"), ("AI4K12", "https://ai4k12.org"),
        ("Machine Learning for Kids", "https://machinelearningforkids.co.uk"), ("Semi-Conductor", "https://semiconductor.withgoogle.com"),
        ("AutoDraw", "https://www.autodraw.com")]),
    "languages": ("kids learning a new language", "#KidsLanguages", [
        ("Duolingo", "https://www.duolingo.com"), ("Google Translate", "https://translate.google.com"),
        ("Lingokids", "https://lingokids.com"), ("Gus on the Go", "https://www.gusonthego.com"),
        ("Memrise", "https://www.memrise.com"), ("Busuu", "https://www.busuu.com"), ("Babbel", "https://www.babbel.com")]),
    "music": ("kids who love music", "#KidsMusic", [
        ("Chrome Music Lab", "https://musiclab.chromeexperiments.com"), ("Blob Opera", "https://artsandculture.google.com/experiment/blob-opera/AAHWrq360NcGbw"),
        ("BandLab", "https://www.bandlab.com"), ("Simply Piano", "https://www.hellosimply.com/simply-piano"),
        ("Yousician", "https://yousician.com"), ("Suno", "https://suno.com"), ("Song Maker", "https://musiclab.chromeexperiments.com/Song-Maker/"),
        ("Soundtrap", "https://www.soundtrap.com"), ("Flowkey", "https://www.flowkey.com")]),
}

EVERYDAY = {
    "writing": ("writing emails and messages", "#WritingTips", [
        ("Grammarly", "https://www.grammarly.com"), ("QuillBot", "https://quillbot.com"), ("Wordtune", "https://www.wordtune.com"),
        ("Goblin Tools", "https://goblin.tools"), ("Hemingway Editor", "https://hemingwayapp.com"),
        ("Microsoft Copilot", "https://copilot.microsoft.com"), ("LanguageTool", "https://languagetool.org"),
        ("Lex", "https://lex.page"), ("Ginger", "https://www.gingersoftware.com")]),
    "photos": ("fixing your photos", "#PhotoEditing", [
        ("remove.bg", "https://www.remove.bg"), ("Photoroom", "https://www.photoroom.com"), ("Picsart", "https://picsart.com"),
        ("Cleanup.pictures", "https://cleanup.pictures"), ("Upscayl", "https://upscayl.org"),
        ("Canva Magic Edit", "https://www.canva.com/features/magic-edit/"), ("Fotor", "https://www.fotor.com"),
        ("Pixlr", "https://pixlr.com")]),
    "travel": ("planning a trip", "#TravelTips", [
        ("Wanderlog", "https://wanderlog.com"), ("Mindtrip", "https://mindtrip.ai"), ("Layla", "https://layla.ai"),
        ("Google Translate", "https://translate.google.com"), ("DeepL", "https://www.deepl.com"),
        ("TripIt", "https://www.tripit.com"), ("GuideGeek", "https://guidegeek.com"),
        ("Trip Planner AI", "https://trip-planner.ai"), ("Kayak", "https://www.kayak.com"),
        ("Hopper", "https://hopper.com"), ("Skyscanner", "https://www.skyscanner.net"),
        ("Google Maps", "https://www.google.com/maps/about/")]),
    "cooking": ("cooking with what is in your fridge", "#EasyRecipes", [
        ("SuperCook", "https://www.supercook.com"), ("DishGen", "https://www.dishgen.com"), ("ChefGPT", "https://www.chefgpt.xyz"),
        ("Samsung Food", "https://samsungfood.com"), ("Mealime", "https://www.mealime.com"), ("Plan to Eat", "https://www.plantoeat.com"),
        ("Paprika", "https://www.paprikaapp.com"), ("Tasty", "https://tasty.co"),
        ("Eat This Much", "https://www.eatthismuch.com"), ("Recipe Keeper", "https://www.recipekeeperonline.com")]),
    "organised": ("getting organised", "#Productivity", [
        ("Goblin Tools", "https://goblin.tools"), ("Todoist", "https://www.todoist.com"), ("Notion", "https://www.notion.com"),
        ("Reclaim", "https://reclaim.ai"), ("Motion", "https://www.usemotion.com"), ("Otter.ai", "https://otter.ai"),
        ("TickTick", "https://ticktick.com"), ("Google Keep", "https://keep.google.com")]),
    "learning": ("learning something new", "#LearnSomethingNew", [
        ("NotebookLM", "https://notebooklm.google"), ("Khan Academy", "https://www.khanacademy.org"),
        ("Duolingo", "https://www.duolingo.com"), ("Perplexity", "https://www.perplexity.ai"), ("Speak", "https://www.speak.com"),
        ("Coursera", "https://www.coursera.org"), ("edX", "https://www.edx.org"), ("Babbel", "https://www.babbel.com"),
        ("Memrise", "https://www.memrise.com"), ("Udemy", "https://www.udemy.com"),
        ("Wolfram Alpha", "https://www.wolframalpha.com")]),
    "accessibility": ("seeing, hearing and reading", "#Accessibility", [
        ("Be My Eyes", "https://www.bemyeyes.com"), ("Seeing AI", "https://www.seeingai.com"), ("Speechify", "https://speechify.com"),
        ("Otter.ai", "https://otter.ai"), ("Live Transcribe", "https://www.android.com/accessibility/live-transcribe/"),
        ("Envision", "https://www.letsenvision.com"), ("NaturalReader", "https://www.naturalreaders.com"),
        ("Ava", "https://www.ava.me"), ("Voice Dream", "https://www.voicedream.com")]),
    "home": ("everyday life at home", "#LifeHacks", [
        ("Google Lens", "https://lens.google"), ("PictureThis", "https://www.picturethisai.com"),
        ("Merlin Bird ID", "https://merlin.allaboutbirds.org"), ("Seek by iNaturalist", "https://www.inaturalist.org/pages/seek_app"),
        ("Microsoft Copilot", "https://copilot.microsoft.com"), ("Gemini", "https://gemini.google.com"),
        ("Shazam", "https://www.shazam.com"), ("Picture Insect", "https://www.pictureinsect.com"),
        ("Cleanup.pictures", "https://cleanup.pictures"), ("Wolfram Alpha", "https://www.wolframalpha.com")]),
}

CATALOGS = {"kids": KIDS, "everyday": EVERYDAY}
WAITING = ("QUEUED", "GENERATING", "RENDERING", "QC", "READY", "RETRY")


def run(conn):
    """Queue a Short for each sister channel due today that has none waiting."""
    from . import db
    made = 0
    for key in channels.enabled():
        if not channels.short_due(key):
            continue
        waiting = conn.execute(
            f"SELECT COUNT(*) FROM topics WHERE source LIKE ? AND status IN ({','.join('?' * len(WAITING))})",
            (key + ":%", *WAITING)).fetchone()[0]
        if waiting:
            continue
        if not publish.platforms(key):  # accounts not connected yet: make nothing that would pile up unposted
            log.info("%s: no YouTube token or Zernio account connected yet; no Short made", key)
            continue
        catalog = CATALOGS[key]
        for cat in tools.candidates(conn, catalog, key, REUSE_DAYS)[:2]:  # each try checks ~8 sites in a browser
            if tools.build(conn, cat, config.CHANNEL_TOOLS_PER_VIDEO, catalog, key):
                made += 1
                break
            db.kv_set(conn, f"{key}_failed:{cat}", str(time.time()))
            log.info("%s category %s skipped for %d days", key, cat, tools.FAILED_RETRY_DAYS)
    return made
