from pypdf import PdfReader
reader = PdfReader(r'c:\Users\Rosen\Downloads\SUST_Hackathon_Preli_Problem_Statement.pdf')
with open(r'd:\SUST CSE CARNIVAL\Preli\CSECar-SWE_TARGARYENS\_pdf_out.txt', 'w', encoding='utf-8') as f:
    for i, page in enumerate(reader.pages):
        f.write(f'--- PAGE {i+1} ---\n')
        f.write(page.extract_text())
        f.write('\n')
print('OK')