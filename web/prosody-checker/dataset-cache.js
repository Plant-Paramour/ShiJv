const DATABASE_NAME = "shiju-meter-cache";
const datasetMemoryCache = new Map();
let databasePromise;
let versionsPromise;
let meterBundlePromise;

function openDatabase() {
  if (!databasePromise) databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("templates")) database.createObjectStore("templates", { keyPath: "file" });
      if (!database.objectStoreNames.contains("metadata")) database.createObjectStore("metadata", { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  }).catch((error) => { databasePromise = null; throw error; });
  return databasePromise;
}

function readRecord(database, storeName, key) {
  return new Promise((resolve, reject) => {
    const request = database.transaction(storeName).objectStore(storeName).get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function writeRecords(database, storeName, records) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(storeName, "readwrite");
    const store = transaction.objectStore(storeName);
    for (const record of records) store.put(record);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error("数据加载失败：" + response.status);
  return response.json();
}

export function getDatasetVersions() {
  if (!versionsPromise) versionsPromise = (async () => {
    const database = await openDatabase();
    const cached = await readRecord(database, "metadata", "dataset-versions");
    try {
      let remote;
      try {
        remote = await fetchJson(new URL("../../v1/poetry/jobs/meter-version", import.meta.url), { cache: "no-store" });
      } catch {
        const [meter, rhyme] = await Promise.all([
          fetchJson(datasetUrl("meter", "meter-version.json"), { cache: "no-store" }),
          fetchJson(datasetUrl("rhyme", "rhyme-version.json"), { cache: "no-store" }),
        ]);
        remote = { meter_version: meter.meter_version, rhyme_version: rhyme.rhyme_version };
      }
      if (!remote.meter_version || !remote.rhyme_version) throw new Error("服务器未提供完整的数据版本");
      const meterChanged = cached?.meterVersion !== remote.meter_version;
      const rhymeChanged = cached?.rhymeVersion !== remote.rhyme_version;
      const changed = meterChanged || rhymeChanged;
      const checkedAt = new Date().toISOString();
      if (changed) {
        await refreshChangedDatasets(database, remote.meter_version, remote.rhyme_version, { meterChanged, rhymeChanged });
        datasetMemoryCache.clear();
        meterBundlePromise = null;
        await new Promise((resolve, reject) => {
          const transaction = database.transaction(["templates", "metadata"], "readwrite");
          transaction.objectStore("metadata").put({
            key: "dataset-versions", meterVersion: remote.meter_version,
            rhymeVersion: remote.rhyme_version, storedAt: checkedAt, checkedAt,
          });
          transaction.oncomplete = resolve;
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(transaction.error);
        });
      } else {
        await writeRecords(database, "metadata", [{ ...cached, checkedAt }]);
      }
      return { database, meterVersion: remote.meter_version, rhymeVersion: remote.rhyme_version };
    } catch (error) {
      if (cached?.meterVersion && cached?.rhymeVersion) {
        return { database, meterVersion: cached.meterVersion, rhymeVersion: cached.rhymeVersion };
      }
      throw error;
    }
  })().finally(() => { versionsPromise = null; });
  return versionsPromise;
}

function datasetUrl(kind, file) {
  const folder = kind === "rhyme" ? "Rhyme" : "Songci_Meter";
  return new URL(`../../${folder}/${file}`, import.meta.url);
}

async function refreshChangedDatasets(database, meterVersion, rhymeVersion, { meterChanged, rhymeChanged }) {
  const files = [];
  if (rhymeChanged) {
    files.push(...["Xinyun.json", "Pinshui.json", "Cilin.json", "Tongyun.json"].map((file) => ({ kind: "rhyme", file, version: rhymeVersion })));
  }
  if (meterChanged) {
    const catalog = await fetchJson(datasetUrl("meter", "index.json"));
    const meterFiles = (catalog.items || catalog).map((item) => typeof item === "string" ? item : item.file).filter(Boolean);
    files.push(...meterFiles.map((file) => ({ kind: "meter", file, version: meterVersion })), { kind: "meter", file: "index.json", version: meterVersion });
  }
  const records = await Promise.all(files.map(async ({ kind, file, version }) => ({ file: `${kind}:${file}`, datasetVersion: version, storedAt: new Date().toISOString(), data: await fetchJson(datasetUrl(kind, file), { cache: "no-store" }) })));
  await writeRecords(database, "templates", records);
  for (const record of records) datasetMemoryCache.delete(record.file);
}

export async function getDatasetJson(kind, file) {
  // Recheck versions even when this page already has the dataset in memory.
  const versions = await getDatasetVersions();
  const datasetVersion = kind === "rhyme" ? versions.rhymeVersion : versions.meterVersion;
  const key = `${kind}:${file}@${datasetVersion}`;
  if (!datasetMemoryCache.has(key)) {
    const pending = (async () => {
      const recordKey = `${kind}:${file}`;
      const cached = await readRecord(versions.database, "templates", recordKey);
      if (cached?.datasetVersion === datasetVersion) return cached.data;
      const data = await fetchJson(datasetUrl(kind, file), { cache: "no-cache" });
      await writeRecords(versions.database, "templates", [{
        file: recordKey, datasetVersion, storedAt: new Date().toISOString(), data,
      }]);
      return data;
    })().catch((error) => { datasetMemoryCache.delete(key); throw error; });
    datasetMemoryCache.set(key, pending);
  }
  return datasetMemoryCache.get(key);
}

export async function getMeterBundle(files) {
  const { database, meterVersion } = await getDatasetVersions();
  const recordKeys = files.map((file) => `meter:${file}`);
  const cached = await Promise.all(recordKeys.map((key) => readRecord(database, "templates", key)));
  const missing = files.filter((_, index) => cached[index]?.datasetVersion !== meterVersion);
  if (missing.length) {
    if (!meterBundlePromise) {
      const url = new URL("../../v1/poetry/jobs/meter-templates", import.meta.url);
      url.searchParams.set("meter_version", meterVersion);
      meterBundlePromise = fetchJson(url).catch(async () => {
        const items = new Array(files.length);
        let next = 0;
        await Promise.all(Array.from({ length: Math.min(8, files.length) }, async () => {
          while (next < files.length) {
            const index = next++;
            items[index] = { file: files[index], data: await fetchJson(datasetUrl("meter", files[index])) };
          }
        }));
        return { meter_version: meterVersion, items };
      }).then((bundle) => {
        if (bundle.meter_version !== meterVersion) throw new Error("词谱版本已更新，请重新开始匹配");
        const records = bundle.items.map((item) => ({
          file: `meter:${item.file}`, datasetVersion: meterVersion,
          storedAt: new Date().toISOString(), data: item.data,
        }));
        for (const record of records) datasetMemoryCache.set(`${record.file}@${meterVersion}`, Promise.resolve(record.data));
        return writeRecords(database, "templates", records).then(() => bundle);
      }).catch((error) => { meterBundlePromise = null; throw error; });
    }
    await meterBundlePromise;
  }
  return Promise.all(files.map(async (file, index) => {
    const key = `meter:${file}@${meterVersion}`;
    const data = cached[index]?.datasetVersion === meterVersion
      ? cached[index].data
      : await datasetMemoryCache.get(key);
    if (data === undefined) throw new Error(`词谱缓存缺少文件：${file}`);
    return { file, data };
  }));
}
