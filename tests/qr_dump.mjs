// (for test_remote: the Gallery's QR encoder's matrices, as JSON, to compare with segno's)
import { qr } from "../mlgallery/ui/qr.js";
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(([text, version, mask]) => {
  const r = qr(text, { version, mask });
  return { version: r.version, mask: r.mask, rows: r.modules.map((row) => row.map((d) => (d ? 1 : 0)).join("")) };
})));
