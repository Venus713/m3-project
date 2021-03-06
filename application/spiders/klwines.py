import logging
import re

from typing import Iterator, Dict, IO

from scrapy import FormRequest
from scrapy import Selector
from scrapy.exceptions import DropItem
from scrapy.http.request import Request
from scrapy.http.response import Response
from scrapy.crawler import CrawlerProcess

from application.scrapers.spider_scraper import get_spider_settings
from application.spiders.base.abstracts.spider import AbstractSpider
from application.spiders.base.abstracts.pipeline import (
    BaseFilterPipeline,
    BaseIncPipeline
)
from application.spiders.base.abstracts.product import (
    AbstractListProduct,
    AbstractParsedProduct
)


BASE_URL = 'https://www.klwines.com'


def get_qoh(response):
    rows = response.xpath(
        '//div[@class="inventory clearfix"]/div[@class="column"]//tr'
    )
    qoh = 0
    for row in rows[1:]:
        qty = AbstractListProduct.clean(
            row.xpath('td/text()')[-1].extract()
        )
        qty = qty.replace('>', '').replace('<', '')
        qoh += int(qty)
    return qoh


def normalize_name(name: str) ->str:
    name = re.sub(r' \((Elsewhere|Previously) \$\d*\)',
                  '',
                  name)
    return name


class ParsedListPageProduct(AbstractListProduct):

    def get_name(self) -> str:
        name = self.s.xpath(
            'div[@class="productImg"]/a/@title'
        ).extract_first()
        name = self.clean(name or '')
        name = normalize_name(name)
        return name

    def get_price(self) -> float:
        s = self.s.xpath(
            'div/span[@class="price"]/span/span/strong/text()'
        ).extract_first()
        if not s:
            return 0
        s = self.clean(s)
        s = s.replace('$', '').replace(',', '')
        try:
            float_s = float(s)
        except ValueError:
            return "ERROR READING PRICE"
        else:
            return float_s

    def get_qoh(self):
        pass

    def get_url(self):
        relative_url = self.s.xpath(
            'div[@class="result-desc"]/a/@href'
        ).extract_first()
        return f'{BASE_URL}{relative_url}'


class ParsedProduct(AbstractParsedProduct):

    def get_sku(self) -> str:
        value = self.r.xpath(
            '//span[@class="SKUInformation"]/text()'
        ).extract()[0]
        value = value.replace('SKU #', '')
        return value

    def get_name(self) -> str:
        name = self.clean(self.r.xpath(
            '//div[@class="result-desc"]/h1/text()'
        ).extract_first())
        name = normalize_name(name)
        return name

    def get_vintage(self) -> str:
        res = ''
        match = re.match(r'.*([1-3][0-9]{3})', self.name)
        if match:
            res = match.group(1)
        return res

    def get_price(self) -> float:
        s = self.clean(self.r.xpath(
            '//div[@class="result-info"]/span/span/strong/text()'
        )[0].extract())
        s = s.replace('$', '').replace(',', '')
        try:
            float_s = float(s)
        except ValueError:
            return "ERROR READING PRICE"
        else:
            return float_s

    def get_image(self) -> str:
        return self.r.xpath('//img[@class="productImg"]/@src')[
            0].extract()

    def get_additional(self):
        additional = {
            'varietals': [],
            'alcohol_pct': None,
            'name_varietal': None,
            'region': None,
            'description': None,
            'other': None,
        }
        rows = self.r.xpath('//div[@class="addtl-info-block"]/table/tr')
        detail_xpath_value = 'td[@class="detail_td"]/h3/text()'
        title_xpath = 'td[@class="detail_td1"]/text()'
        for row in rows:
            title = self.clean(row.xpath(title_xpath).extract()[0])
            if title == "Alcohol Content (%):":
                value = self.clean(row.xpath(
                    'td[@class="detail_td"]/text()').extract()[0])
                additional['alcohol_pct'] = value
            else:
                values = row.xpath(detail_xpath_value).extract()
                value = values and values[0].replace(" and ", " ")
                if title == "Varietal:":
                    description = self.clean(
                        row.xpath(
                            'td[@class="detail_td"]/text()').extract()[1])
                    additional['description'] = description

                    additional['name_varietal'] = value
                    if value:
                        additional['varietals'].append(value)
                elif title in ("Country:",
                               "Sub-Region:",
                               "Specific Appellation:"):
                    additional['region'] = value
        return additional

    def get_reviews(self) -> list:
        reviews = []
        reviewer_point = self.r.xpath(
            '//div[@class="result-desc"]/span[@class="H2ReviewNotes"]'
        )
        texts = self.r.xpath(
            '//div[@class="result-desc"]/p'
        )
        for rp, text in zip(reviewer_point, texts):
            reviewer_name = self.clean(
                ''.join(rp.xpath('text()').extract())
            )
            content = self.clean(''.join(text.xpath('text()').extract()))
            if reviewer_name:
                reviewer_name = self.match_reviewer_name(reviewer_name)

            raw_points = rp.xpath('span/text()')
            if raw_points:
                score = self.clean(
                    raw_points[0].extract()
                ).replace('points', '').strip()
                if '-' in score:
                    score = score.split('-')[-1]
            else:
                score = None
            reviews.append({
                'reviewer_name': reviewer_name,
                'score_num': score and int(score),
                'score_str': score,
                'content': content
            })
        return reviews

    def get_qoh(self) -> int:
        qoh = get_qoh(self.r)
        return qoh

    def as_dict(self) -> Dict:
        return self.result


class KLWinesSpider(AbstractSpider):
    """'Spider' which is getting data from klwines.com"""

    name = 'klwines'
    LOGIN = "wine_shoper@protonmail.com"
    PASSWORD = "ilovewine1B"
    filter_pipeline = "application.spiders.klwines.FilterPipeline"
    inc_filter_pipeline = "application.spiders.klwines.IncFilterPipeline"

    def start_requests(self) -> Iterator[Dict]:
        yield Request(
            BASE_URL,
            callback=self.before_login
        )

    def before_login(self, _: Response) -> Iterator[Dict]:
        yield Request(
            f'{BASE_URL}/account/login',
            callback=self.login
        )

    def login(self, response: Response) -> Iterator[Dict]:
        token_path = response.xpath(
            '//div[contains(@class,"login-block")]'
            '//input[@name="__RequestVerificationToken"]/@value')
        token = token_path[0].extract()
        return FormRequest.from_response(
            response,
            formxpath='//*[contains(@action,"login")]',
            formdata={'Email': self.LOGIN,
                      'Password': self.PASSWORD,
                      '__RequestVerificationToken': token,
                      'Login.x': "24",
                      'Login.y': "7"},
            callback=self.parse_wine_types
        )

    def is_not_logged(self, response):
        return "Welcome, John" not in response.body.decode('utf-8')

    def get_wine_types(self, response: Response) -> list:
        res = []
        rows = response.xpath(
            '//ul[@id="category-menu-container-ProductType"]/li/a')[::-1]
        for row in rows:
            wine_filter = row.xpath('@href').extract()[0]
            # wine_type = row.xpath('@span').extract()[0]  # prev version, changed Apr 2 2019
            wine_type = row.xpath('span/text()')[0].extract()
            wine_type = wine_type.replace('Wine - ', '')
            wines_total = row.xpath('span[2]/text()').extract()[0]
            wines_total = int(wines_total[1:-1])
            if 'misc' in wine_type.lower():
                continue
            res.append((wine_type, wines_total, wine_filter))
        return res

    def get_listpages(self, response: Response) -> Iterator[Dict]:
        wine_types = self.get_wine_types(response)
        step = 500
        items_scraped = 0
        for (wine_type, wines_total, wine_filter) in wine_types:
            wine_filter = wine_filter.replace('limit=50', f'limit={step}')
            wine_filter = wine_filter.replace('&offset=0', '')
            if wines_total % step or wines_total < step:
                wines_total += step

            for offset in range(0, wines_total, step):

                items_scraped += offset
                url = f'{wine_filter}&offset={offset}'
                offset += step
                yield Request(
                    f'{BASE_URL}{url}',
                    meta={'wine_type': wine_type},
                    callback=self.parse_listpage,
                )

    def parse_wine_types(self, response: Response) -> Iterator[Dict]:
        if self.is_not_logged(response):
            self.logger.exception("Login failed")
            yield
        else:
            # url contains filters 750ml+In Stock(Not Pre-arrival)
            url = ('Products?&filters=sv2_30$eq$(227)$True$w$or,30$eq$(230)$'
                   'True$$.or,30$eq$(225)$True$$.or,30$eq$(229)$True$$.or,30'
                   '$eq$(226)$True$$.or,30$eq$(228)$True$$!243!42$eq$0$True$'
                   'ff-42-0--$&limit=50&offset=0&orderBy=&searchText=')
            wines_url = f'{BASE_URL}/{url}'
            yield Request(wines_url,
                          callback=self.get_listpages)

    def parse_listpage(self, response: Response) -> Iterator[Dict]:
        """Process http response
        :param response: response from ScraPy
        :return: iterator for data
        """
        if self.is_not_logged(response):
            self.logger.exception("Login failed")
            yield
        else:
            full_scrape = self.settings['FULL_SCRAPE']
            rows = response.xpath(
                "//div[contains(concat(' ', @class, ' '), ' result ')]")
            links = []
            for row in rows:
                if full_scrape:
                    if row:
                        if 'auctionResult-desc' not in row.extract():
                            link = row.xpath(
                                'div[@class="result-desc"]/a/@href'
                            ).extract_first()
                            if link:
                                links.append(link)
                            else:
                                logging.exception(
                                    f'Link not fount for {row} '
                                    f'on page: {response.url}'
                                )
                else:
                    if row:
                        yield self.parse_list_product(row)
            for link in links:
                absolute_url = BASE_URL + link
                yield Request(
                    absolute_url,
                    callback=self.parse_product,
                    meta={'wine_type': response.meta.get('wine_type')},
                    priority=1)

    def get_product_dict(self, response: Response):
        return ParsedProduct(response).as_dict()

    def get_list_product_dict(self, s: Selector):
        return ParsedListPageProduct(s).as_dict()


class FilterPipeline(BaseFilterPipeline):

    IGNORED_IMAGES = [
        'genericred-l.jpg',
        'genericred-xl.jpg',
        'shiner_red_burgundy_l.jpg',
        'shiner_white_l.jpg',
        'shiner_white_burgundy_l.jpg',
        'shiner_riesling_l.jpg',
        'shiner_sparkling_l.jpg',
        'shiner_sauternes_l.jpg',
        'shiner_port_l.jpg',
    ]

    def _check_multipack(self, item: dict):
        regex = re.compile('.*(pack in OWC).*', re.IGNORECASE)
        if bool(regex.match(item['name'])):
            raise DropItem(f'Ignoring multipack product: {item["name"]}')


class IncFilterPipeline(BaseIncPipeline):

    def get_qoh(self, response):
        return get_qoh(response)

    def parse_detail_page(self, response):
        product = ParsedProduct(response)
        yield product.as_dict()


def get_data(tmp_file: IO) -> None:
    settings = get_spider_settings(tmp_file, 1, KLWinesSpider, full_scrape=True)
    process = CrawlerProcess(settings)
    process.crawl(KLWinesSpider)
    process.start()


if __name__ == '__main__':
    import os
    from application import create_app
    app = create_app()
    with app.app_context():
        current_path = os.getcwd()
        file_name = os.path.join(current_path, 'klwines.txt')
        with open(file_name, 'w') as out_file:
            get_data(out_file)
