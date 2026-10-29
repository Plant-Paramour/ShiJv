// 单机版直接读取本机数据，不依赖数据版本或浏览器持久化缓存。
function datasetUrl(kind, file) {
  const folder = kind === "rhyme" ? "Rhyme" : "Songci_Meter";
  return new URL(`../../${folder}/${file}`, import.meta.url);
}

export async function getDatasetJson(kind, file) {
  const response = await fetch(datasetUrl(kind, file), { cache: "no-store" });
  if (!response.ok) throw new Error("数据加载失败：" + response.status);
  return response.json();
}

export async function getMeterBundle(files) {
  return Promise.all(files.map(async (file) => ({
    file,
    data: await getDatasetJson("meter", file),
  })));
}
