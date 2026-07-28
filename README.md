# Metabolit literatür sayımı — PubMed / Europe PMC

Literatür sıklığı çalışma kitabındaki her matris sayfası için metabolit başına
yayın sayısı çeker, sonuçları kitaba yazar ve iki kaynağın sıralamasını
karşılaştırır.

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Paket içeriği

```
app.py             Streamlit arayüzü, çalışma kitabı okuma/yazma
backends.py        PubMed + Europe PMC sorgu kurulumu, HTTP, hız sınırı
cache.py           SQLite kalıcı önbellek
i18n.py            Çeviri yükleme ve dil seçici
branding.py        Logo bulma ve tıklanabilir yerleşim
test_smoke.py      Ağ gerektirmeyen duman testi
requirements.txt
locales/           tr.json  en.json  de.json  fr.json
assets/            logo.png  icon.png
```

Kurulumdan sonra `python test_smoke.py` ile her şeyin yerinde olduğunu
doğrulayabilirsiniz; bu test ağa çıkmaz.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `app.py` | Streamlit arayüzü, çalışma kitabı okuma/yazma |
| `backends.py` | PubMed (E-utilities) ve Europe PMC sorgu kurulumu + HTTP + hız sınırı |
| `cache.py` | SQLite kalıcı önbellek (sorgu → sayım, zaman damgalı) |
| `branding.py` | Logo bulma ve tıklanabilir yerleşim |
| `i18n.py` | Çeviri yükleme, dil seçici |
| `locales/` | `tr.json` `en.json` `de.json` `fr.json` |
| `assets/` | Logo dosyası buraya konur |
| `test_smoke.py` | Ağ gerektirmeyen duman testi (`python test_smoke.py`) |

## Diller

Arayüz Türkçe, İngilizce, Almanca ve Fransızca çalışır. Dil kenar çubuğunun
tepesinden seçilir ve adres satırına yazılır — `...?lang=de` bağlantısı karşı
tarafta doğrudan Almanca açılır. Varsayılan `APP_LANG` ortam değişkeniyle de
verilebilir (`APP_LANG=en streamlit run app.py`); atanmazsa Türkçe açılır.

Metinler `locales/<kod>.json` içinde düz anahtar → dize eşlemesidir. Bir çeviriyi
düzeltmek için yalnızca JSON dosyası değişir; ortaklar Python'a dokunmadan
katkı verebilir. Yeni dil için dosyayı kopyalayıp çevirin ve `i18n.py` içindeki
`LANGUAGES` sözlüğüne ekleyin.

Eksik bir anahtar İngilizceye, o da yoksa anahtarın kendisine düşer; çeviri
yarım kalmışken de uygulama çalışır. `test_smoke.py` dört dosyanın anahtar
kümesinin aynı olduğunu ve boş çeviri bulunmadığını denetler.

Dilden bağımsız kalanlar:

* **Arama sorguları** — PubMed ve Europe PMC alan adları, matris terimleri,
  metabolit adları. Sorgu dili değişirse sayım karşılaştırılamaz hâle gelir.
* **Log CSV başlıkları** — makine tarafından okunan yeniden üretilebilirlik
  kaydı; sabit İngilizce.
* **Arka uç hata mesajları** — teknik tanı bilgisi.

Çalışma kitabına yazılan karşılaştırma sütunlarının başlıkları ise koşu
sırasındaki dilde yazılır (`PubMed sıra`, `PubMed Rang`, `PubMed rang` …).

## Logo

```
assets/logo.png   tam logo (2540×852)     → kenar çubuğu tepesi + başlık yanı
assets/icon.png   altıgen işaret (512²)   → favicon + kapalı kenar çubuğu
```

Her ikisi de tıklanabilir ve <https://www.met4metab.ptb.de/introduction>
adresini yeni sekmede açar. Adres `branding.py` içindeki `MET4METAB_URL`
sabitinde. Dosyalar yoksa uygulama sessizce logosuz açılır.

Kare işaret ayrıca türetilebilir; yatay logo doğrudan favicon yapılırsa ezilir.
Farklı yollar için `APP_LOGO` ve `APP_ICON` ortam değişkenleri kullanılabilir.

Görsel base64 olarak gömülür (ayrıca statik sunum gerekmez), gösterim boyutuna
indirgenip önbelleklenir — betik her etkileşimde yeniden çalıştığı için tam
çözünürlüklü dosyayı her seferinde göndermek gereksiz (95 KB → 28 KB).

**Koyu tema.** Logonun lacivertİ Streamlit'in koyu arka planında neredeyse
görünmez. Resmî logonun renkleri değiştirilmez; bunun yerine koyu tema
algılandığında logo açık renkli bir zemin üzerine alınır. Bunu istemiyorsanız
`.streamlit/config.toml` ile temayı sabitleyin:

```toml
[theme]
base = "light"
```

## Kaynaklar

**PubMed** — NCBI E-utilities. Anahtarsız 3 istek/s, anahtarla 10 istek/s.
Anahtar zorunlu değildir; alan boş bırakılırsa istekler arası 0,40 s uygulanır.
Sorgu dili orijinal betikle birebir aynıdır.

**Europe PMC** — EBI Articles RESTful API. Anahtar veya kayıt gerektirmez.
Alan karşılıkları: `[TIAB]` → `TITLE_ABS`, `[PDAT]` → `PUB_YEAR`,
`humans[MH]` → `MESH_TERMS:"Humans"`, ayrıca `SRC:MED` ile MEDLINE alt kümesine
indirgeme.

> `synonym` parametresi her istekte açıkça gönderilir. Açık bırakılırsa Europe PMC
> sorguyu MeSH/UniProt eşanlamlılarıyla genişletir ve sayım artık `SYNONYMS`
> sözlüğüne izlenebilir olmaktan çıkar. Varsayılan: kapalı.

İnsan filtresinin alan adı arayüzden değiştirilebilir; Advanced Search Query
Builder ile bir kez doğrulayıp "Tek sorgu testi" bölümünde deneyin.

## Karşılaştırma

"İkisi" seçildiğinde her sayfa için Spearman sıra korelasyonu, ilk N örtüşmesi
ve sıra saçılım grafiği hesaplanır. İkincil kaynağın sayımı, sıraları ve Δ sıra
çalışma kitabında I sütunundan itibaren yer alır.

İki kaynağın mutlak sayıları eşleşmez — farklı korpus, farklı indeksleme.
Anlamlı olan sıralamanın uyumudur: yüksek ρ, metabolit seçiminin veri tabanı
seçimine duyarlı olmadığını gösterir.

## Önbellek

Sorgu metni + kaynak anahtarıyla SQLite'a yazılır, her kayıt zaman damgalıdır.
Geçerlilik süresi (gün) aşılmış kayıtlar yeniden çekilir; 0 girilirse önbellek
okunmaz. Yarıda kalan bir koşu tekrar başlatıldığında yalnızca eksik sorgular
ağa çıkar.

Streamlit Community Cloud'da dosya sistemi kalıcı değildir — koşu sonrası
önbelleği CSV olarak dışa aktarıp bir sonraki oturumda geri yükleyin.
Yerel kullanımda `COUNT_CACHE` ortam değişkeni ile yol belirlenebilir.

## Dağıtım notu

Uygulamayı ortaklarla paylaşacaksanız kendi NCBI anahtarınızı `secrets.toml`'a
gömmeyin: NLM, üçüncü taraf uygulamaların her kullanıcının kendi anahtarını
girmesine izin vermesini istiyor ve ortak çıkış IP'si anahtarı hızla tüketir.
Anahtar alanı boş bırakılabilir; Europe PMC zaten anahtarsız çalışır.

## Raporlama

Log CSV her satır için kaynağı, sorguyu, sayımı, önbellekten gelip gelmediğini
ve çekilme zamanını taşır. Bir sayım, kaynağı ve tarihi olmadan raporlanamaz.
