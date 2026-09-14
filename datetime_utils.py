from datetime import datetime, timedelta, timezone

DATE_FORMAT = "%Y/%m/%d %H:%M:%S"
DATE_FORMAT_ISO = "%Y-%m-%dT%H:%M:%S.000Z"
JST = timezone(timedelta(hours=9), "JST")


def dateTime2String(dateTime):
    if dateTime.tzinfo is None or dateTime.utcoffset() is None:
        dateTime = dateTime.replace(tzinfo=JST)
    return dateTime.astimezone(JST).strftime(DATE_FORMAT)


def string2DateTime(str):
    dateTime = datetime.strptime(str, DATE_FORMAT).replace(tzinfo=JST)
    return dateTime.astimezone(timezone.utc)


def isoString2DateTime(value):
    dateTime = datetime.strptime(value, DATE_FORMAT_ISO)
    return dateTime.replace(tzinfo=timezone.utc)


def calcDate(datetime, days):
    if datetime.tzinfo is None or datetime.utcoffset() is None:
        datetime = datetime.replace(tzinfo=JST)
    return (datetime - timedelta(days=days)).astimezone(timezone.utc)


def now():
    return datetime.now(JST)


def nowString():
    return dateTime2String(now())


def main():
    print(now())
    print(dateTime2String(now()))
    print(string2DateTime("2025/09/05 22:39:31"))


if __name__ == "__main__":
    main()
