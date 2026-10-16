// pdfscope's OCR helper: Apple Vision's document reader (macOS 26 or later).
// Reads one image path per line on stdin and writes one JSON object per line on stdout:
// text lines, paragraphs, tables (cells with row and column spans) and lists, each with a box.
// Boxes are fractions of the image, origin top left: [x0, y0, x1, y1].
import Foundation
import Vision

struct Line: Codable { let text: String; let box: [Double] }
struct Cell: Codable { let text: String; let rows: [Int]; let cols: [Int]; let box: [Double] }
struct Table: Codable { let box: [Double]; let cells: [Cell] }
struct ListOut: Codable { let box: [Double]; let items: [String] }
struct Page: Codable {
    let lines: [Line]; let words: [Line]; let textLines: [Line]; let paragraphs: [Line]; let tables: [Table]; let lists: [ListOut]
    let title: String?; let error: String?
}

func box(_ r: NormalizedRect) -> [Double] {
    [r.origin.x, 1 - r.origin.y - r.height, r.origin.x + r.width, 1 - r.origin.y]
}

setvbuf(stdout, nil, _IOLBF, 0)
let encoder = JSONEncoder()
while let path = readLine(strippingNewline: true) {
    var request = RecognizeDocumentsRequest()
    request.textRecognitionOptions.useLanguageCorrection = true
    let langs = ProcessInfo.processInfo.environment["PDFSCOPE_LANGUAGES"] ?? "en-US"
    request.textRecognitionOptions.automaticallyDetectLanguage = langs == "auto"
    if langs != "auto" {
        request.textRecognitionOptions.recognitionLanguages = langs.split(separator: ",").map { Locale.Language(identifier: String($0)) }
    }
    var page = Page(lines: [], words: [], textLines: [], paragraphs: [], tables: [], lists: [], title: nil, error: nil)
    do {
        let observations = try await request.perform(on: URL(fileURLWithPath: path))
        var lines: [Line] = [], words: [Line] = [], paragraphs: [Line] = [], tables: [Table] = [], lists: [ListOut] = []
        var title: String? = nil
        for obs in observations {
            let doc = obs.document
            lines += doc.text.lines.map { Line(text: $0.transcript, box: box($0.boundingBox)) }
            words += (doc.text.words ?? []).map { Line(text: $0.transcript, box: box($0.boundingBox)) }
            paragraphs += doc.paragraphs.map { Line(text: $0.transcript, box: box($0.boundingRegion.boundingBox)) }
            tables += doc.tables.map { t in
                Table(box: box(t.boundingRegion.boundingBox), cells: t.rows.flatMap { $0 }.map {
                    Cell(text: $0.content.text.transcript, rows: [$0.rowRange.lowerBound, $0.rowRange.upperBound],
                         cols: [$0.columnRange.lowerBound, $0.columnRange.upperBound],
                         box: box($0.content.boundingRegion.boundingBox))
                })
            }
            lists += doc.lists.map { l in ListOut(box: box(l.boundingRegion.boundingBox), items: l.items.map { $0.content.text.transcript }) }
            title = title ?? doc.title?.transcript
        }
        // the classic line reader segments dense pages into lines better than the document reader
        var textRequest = RecognizeTextRequest()
        textRequest.recognitionLevel = .accurate
        textRequest.usesLanguageCorrection = true
        textRequest.automaticallyDetectsLanguage = request.textRecognitionOptions.automaticallyDetectLanguage
        if !textRequest.automaticallyDetectsLanguage {
            textRequest.recognitionLanguages = request.textRecognitionOptions.recognitionLanguages
        }
        let textLines = try await textRequest.perform(on: URL(fileURLWithPath: path)).map {
            Line(text: $0.topCandidates(1).first?.string ?? "", box: box($0.boundingBox))
        }
        page = Page(lines: lines, words: words, textLines: textLines, paragraphs: paragraphs, tables: tables, lists: lists, title: title, error: nil)
    } catch {
        page = Page(lines: [], words: [], textLines: [], paragraphs: [], tables: [], lists: [], title: nil, error: "\(error)")
    }
    print(String(data: try! encoder.encode(page), encoding: .utf8)!)
}
